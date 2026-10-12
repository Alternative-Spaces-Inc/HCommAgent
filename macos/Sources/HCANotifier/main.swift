import AppKit
import UserNotifications
import os
import HCACore

/// HCA Notifier: menu bar item, native notifications with action buttons for
/// pending asks, signed calls on the device key. Must run from the .app
/// bundle built by scripts/bundle.sh (UNUserNotificationCenter refuses a bare
/// executable).
///
/// Day 1: menu, permission, test banners. Day 2: Keychain device key.
/// Day 3: signed inbox read, SSE stream with poll fallback, signed answer,
/// dedupe between stream and poll.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private let statusMenuItem = NSMenuItem(title: "Status: starting", action: nil, keyEquivalent: "")
    private let connectionItem = NSMenuItem(title: "Server: not configured", action: nil, keyEquivalent: "")
    private let pendingItem = NSMenuItem(title: "Pending asks (0)", action: nil, keyEquivalent: "")
    // HCA-P15: one row per pending ask, rebuilt on every inbox delta.
    private var askRowItems: [NSMenuItem] = []
    private let noAsksItem = NSMenuItem(title: "No pending asks", action: nil, keyEquivalent: "")
    private var menu: NSMenu!
    private let lastActionItem = NSMenuItem(title: "Last action: none", action: nil, keyEquivalent: "")
    private let deviceItem = NSMenuItem(title: "Device: loading", action: nil, keyEquivalent: "")
    private let priorityItem = NSMenuItem(title: "Priority: loading", action: nil, keyEquivalent: "")
    // HCA-P18: liveness row and the launch-at-login toggle.
    private let livenessItem = NSMenuItem(title: "Last inbox read: waiting for the first read", action: nil, keyEquivalent: "")
    private let privacyItem = NSMenuItem(title: "Hide decision details in notifications", action: #selector(toggleNotificationPrivacy), keyEquivalent: "")
    private var redactNotifications: Bool {
        UserDefaults.standard.object(forKey: "redactNotifications") as? Bool ?? true
    }
    private let loginItem = NSMenuItem(title: "Launch at login", action: #selector(toggleLoginItem), keyEquivalent: "")
    private var liveness = Liveness()
    private var stalenessTask: Task<Void, Never>?
    private let livenessLog = Logger(subsystem: "com.alternativespaces.hca.notifier", category: "liveness")
    private var priorityChoiceItems: [NSMenuItem] = []

    private var signer: DeviceSigner?
    private var client: APIClient?
    private var networkGeneration = 0
    private var store = InboxStore()
    private var submitting: Set<String> = []
    private var answerKeys: [String: (AnswerSubmission, String)] = [:]
    private var shownAt: [String: Date] = [:]
    private var streamTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var streamConnected = false
    /// HCA-P20: last SSE id seen, handed back as Last-Event-ID on every connect,
    /// the first one after a relaunch included (persisted in UserDefaults).
    private let cursor = EventCursor()
    private var lastEventId: String? { cursor.value }
    /// HCA-P20 Day 5 follow-up: one line per banner post with its reason, so a
    /// repeated banner can be told apart (added vs repost) in Console.app.
    private let postLog = Logger(subsystem: "com.alternativespaces.hca.notifier", category: "posts")
    static let pollInterval: UInt64 = 60

    private let isLaunchdInstance = ProcessInfo.processInfo.environment[LaunchAgentPlist.launchdMarker] == "1"

    func applicationDidFinishLaunching(_ notification: Notification) {
        resolveDuplicateInstance()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "checkmark.bubble", accessibilityDescription: "HCA")
            button.toolTip = "Human Collections Agent"
        }
        let menu = NSMenu()
        self.menu = menu
        menu.addItem(statusMenuItem)
        menu.addItem(connectionItem)
        menu.addItem(livenessItem)
        menu.addItem(lastActionItem)
        menu.addItem(.separator())
        menu.addItem(pendingItem)
        menu.addItem(noAsksItem)
        menu.addItem(.separator())
        menu.addItem(deviceItem)
        menu.addItem(priorityItem)
        // HCA-P14 priority controls: pick this Mac's place in the surface ladder.
        // Submenu items name their target explicitly and skip auto-enabling:
        // in the first build the nil-target click inside the submenu did nothing
        // on the owner's Mac (proof take 1), while top-level items worked.
        let prioritySub = NSMenu(title: "Surface priority")
        prioritySub.autoenablesItems = false
        for place in SurfacePriority.allCases {
            let item = NSMenuItem(title: place.title, action: #selector(choosePriority(_:)), keyEquivalent: "")
            item.target = self
            item.isEnabled = true
            item.tag = place.rawValue
            prioritySub.addItem(item)
            priorityChoiceItems.append(item)
        }
        let priorityParent = NSMenuItem(title: "Set this Mac's surface priority", action: nil, keyEquivalent: "")
        priorityParent.submenu = prioritySub
        menu.addItem(priorityParent)
        loginItem.target = self
        menu.addItem(loginItem)
        privacyItem.target = self; privacyItem.state = redactNotifications ? .on : .off
        menu.addItem(privacyItem)
        menu.addItem(NSMenuItem(title: "Copy device public key (PEM)", action: #selector(copyPublicKey), keyEquivalent: "c"))
        menu.addItem(NSMenuItem(title: "Copy device id", action: #selector(copyDeviceId), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Configure API origin…", action: #selector(configureOrigin), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Link Google account (opens browser)", action: #selector(linkGoogle), keyEquivalent: "g"))
        menu.addItem(NSMenuItem(title: "Refresh inbox now", action: #selector(refreshNow), keyEquivalent: "r"))
        #if DEBUG
        menu.addItem(NSMenuItem(title: "Send test notification (Approve / Deny)", action: #selector(sendTestApproveDeny), keyEquivalent: "t"))
        menu.addItem(NSMenuItem(title: "Send test notification (3 options)", action: #selector(sendTestRadio), keyEquivalent: ""))
        #endif
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit HCA Notifier", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        menu.delegate = self
        statusItem.menu = menu
        refreshLoginItemRow()
        livenessLog.info("started pid \(ProcessInfo.processInfo.processIdentifier, privacy: .public) launchd \(self.isLaunchdInstance, privacy: .public) login item \(String(describing: LoginItem.state), privacy: .public)")

        do {
            let s = try KeychainKeyStore.loadOrCreate()
            signer = s
            deviceItem.title = "Device: \(s.deviceId)"
            Self.writePublicKeyFile(s)
            let configured = APIClient(signer: s)
            if let origin = configured.baseURL?.absoluteString {
                let prior = UserDefaults.standard.string(forKey: "trustedAPIOrigin")
                if prior == origin || approveOrigin(origin, prior: prior) { client = configured }
            }
        } catch {
            deviceItem.title = "Device: key error \(error.localizedDescription)"
        }

        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.setNotificationCategories(Self.baseCategories())
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            DispatchQueue.main.async {
                if let error = error {
                    self.statusMenuItem.title = "Status: notification error \(error.localizedDescription)"
                } else if granted {
                    self.statusMenuItem.title = "Status: notifications allowed"
                } else {
                    self.statusMenuItem.title = "Status: notifications denied (System Settings, Notifications, HCA Notifier)"
                }
            }
        }

        startNetwork()
        startStalenessWatch()
        installSignalTrigger()
        healLoginItemAfterRebuild()
    }

    @objc private func toggleNotificationPrivacy() {
        UserDefaults.standard.set(!redactNotifications, forKey: "redactNotifications")
        privacyItem.state = redactNotifications ? .on : .off
        let identifiers = store.pending.keys.map { NotificationShaper.notificationIdentifier(for: $0) }
        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: identifiers)
        UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: identifiers)
        askCategories.removeAll()
        UNUserNotificationCenter.current().setNotificationCategories(Self.baseCategories())
        for item in store.pending.values { post(item: item, reason: "privacy setting changed") }
    }

    private func approveOrigin(_ origin: String, prior: String?) -> Bool {
        let alert = NSAlert()
        alert.messageText = "Trust this HCA API origin?"
        alert.informativeText = (prior.map { "Previous: " + $0 + "\n" } ?? "") + "API: " + origin + "\nDevice-signed requests and answers will be sent to this origin."
        alert.addButton(withTitle: "Cancel"); alert.addButton(withTitle: "Trust origin")
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertSecondButtonReturn else { return false }
        UserDefaults.standard.set(origin, forKey: "trustedAPIOrigin")
        if prior != origin {
            UNUserNotificationCenter.current().getDeliveredNotifications { delivered in
                let stale = delivered.filter { $0.request.content.userInfo["api_origin"] as? String != origin }.map { $0.request.identifier }
                UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: stale)
            }
            UNUserNotificationCenter.current().getPendingNotificationRequests { pending in
                let stale = pending.filter { $0.content.userInfo["api_origin"] as? String != origin }.map { $0.identifier }
                UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: stale)
            }
        }
        cursor.value = nil
        return true
    }

    @objc private func configureOrigin() {
        let alert = NSAlert()
        alert.messageText = "Configure HCA API origin"
        alert.informativeText = "Enter an HTTPS origin with no path, query, credentials, or fragment."
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 440, height: 24))
        input.stringValue = client?.baseURL?.absoluteString ?? "https://"
        input.setAccessibilityLabel("HCA API origin")
        alert.accessoryView = input
        alert.addButton(withTitle: "Cancel"); alert.addButton(withTitle: "Save origin")
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertSecondButtonReturn else { return }
        do {
            let origin = try APIOrigin(input.stringValue, allowLocalHTTP: UserDefaults.standard.bool(forKey: "allowLocalHTTPDevelopment")).url.absoluteString
            guard approveOrigin(origin, prior: client?.baseURL?.absoluteString), let signer else { return }
            networkGeneration += 1
            linkTask?.cancel(); linking = false
            streamTask?.cancel(); pollTask?.cancel()
            client?.session.invalidateAndCancel()
            apply(store.applyInbox([])); answerKeys.removeAll(); shownAt.removeAll(); askCategories.removeAll()
            cursor.value = nil
            UserDefaults.standard.set(origin, forKey: APIClient.defaultsKey)
            client = APIClient(signer: signer)
            streamConnected = false; liveness.streamConnected = false
            startNetwork()
        } catch { postLocal(title: "HCA: invalid API origin", body: "Use an HTTPS origin such as https://api.example.com. Local HTTP requires the documented development setting.") }
    }

    func applicationWillTerminate(_ notification: Notification) {
        networkGeneration += 1
        linkTask?.cancel()
        streamTask?.cancel(); pollTask?.cancel(); stalenessTask?.cancel(); Task { await inboxReads.cancel() }
        client?.session.invalidateAndCancel()
        for source in signalSources { source.cancel() }
    }

    /// A hand-opened instance that finds the agent configured: if the launchd job
    /// is loaded but its instance is not running (the binary was rebuilt and the
    /// job died, or launchd gave up), kickstart it; if the plist is present but
    /// not loaded (after a logout), load it. Then hand over so one icon remains.
    private func healLoginItemAfterRebuild() {
        guard !isLaunchdInstance else { return }
        let st = LoginItem.state
        let action: String
        var err: String?
        switch st {
        case .on:
            if let pid = LaunchAgentPlist.runningPid(launchctlPrint: LoginItem.launchctl(["print", LoginItem.target]).output) {
                livenessLog.info("login item on, launchd instance pid \(pid, privacy: .public) running; this hand-opened instance should have exited already")
                return
            }
            action = "kickstart"; err = LoginItem.kickstart().1
        case .notFound:
            action = "load"; err = LoginItem.load().1
        case .off, .requiresApproval:
            return
        }
        if let err = err {
            livenessLog.error("login item \(action, privacy: .public) failed: \(err, privacy: .public)")
            lastActionItem.title = "Last action: launch at login \(action) failed (\(err))"
            return
        }
        refreshLoginItemRow()
        lastActionItem.title = "Last action: launch at login \(action) after relaunch"
        livenessLog.info("login item \(action, privacy: .public) from a hand-opened instance; handing over to launchd in 2 s")
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { exit(0) }
    }

    // MARK: HCA-P18 liveness and login item

    /// Two instances show two icons (Day 2 finding). The launchd instance wins.
    private func resolveDuplicateInstance() {
        let me = ProcessInfo.processInfo.processIdentifier
        let others = NSWorkspace.shared.runningApplications.filter {
            $0.bundleIdentifier == Bundle.main.bundleIdentifier && $0.processIdentifier != me
        }
        switch LaunchAgentPlist.duplicateAction(otherRunning: !others.isEmpty, selfIsLaunchd: isLaunchdInstance) {
        case .none:
            return
        case .exitSelf:
            livenessLog.info("duplicate instance: hand-opened pid \(me, privacy: .public) exits, \(others.first?.processIdentifier ?? 0, privacy: .public) keeps running")
            exit(0)
        case .terminateOther:
            for app in others {
                livenessLog.info("duplicate instance: launchd pid \(me, privacy: .public) terminates hand-opened pid \(app.processIdentifier, privacy: .public)")
                app.terminate()
            }
        }
    }

    private func recordRead() {
        let wasStale = liveness.isStale(now: Date())
        liveness.recordRead(at: Date())
        if wasStale {
            livenessLog.info("recovered: inbox read after staleness")
            statusMenuItem.title = "Status: recovered, inbox read"
        }
        refreshLivenessRow()
    }

    private func refreshLivenessRow() { livenessItem.title = liveness.title(now: Date()) }

    /// Every 30 s: if the last successful read is older than Liveness.staleAfter,
    /// log it, flag the status line, restart the stream task and poll at once.
    /// Recovery needs no relaunch; the proof blocks the API host for four minutes.
    private func startStalenessWatch() {
        stalenessTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30 * 1_000_000_000)
                guard let self = self else { return }
                await MainActor.run {
                    self.refreshLivenessRow()
                    if self.liveness.isStale(now: Date()) {
                        self.livenessLog.error("stale: no inbox read for \(Int(Date().timeIntervalSince(self.liveness.lastRead ?? Date()))) s, reconnecting")
                        self.statusMenuItem.title = "Status: stale, reconnecting"
                        self.streamTask?.cancel()
                        self.pollTask?.cancel()
                        self.startNetwork()
                    }
                }
            }
        }
    }

    private func refreshLoginItemRow() {
        let state = LoginItem.state
        loginItem.title = LoginItem.menuTitle(state)
        loginItem.state = state == .on ? .on : .off
    }

    @objc private func toggleLoginItem() {
        let before = LoginItem.state
        if before == .on, isLaunchdInstance {
            // bootout stops the launchd job, which is this process. Say so first.
            livenessLog.info("login item on -> off requested from the launchd instance; the app quits, reopen it from the bundle")
        }
        let (after, err) = LoginItem.toggle()
        refreshLoginItemRow()
        if let err = err {
            lastActionItem.title = "Last action: launch at login failed (\(err))"
            livenessLog.error("login item toggle failed from \(String(describing: before), privacy: .public): \(err, privacy: .public)")
        } else {
            lastActionItem.title = "Last action: launch at login \(after == .on ? "on" : after == .requiresApproval ? "needs approval in System Settings" : "off")"
            livenessLog.info("login item \(String(describing: before), privacy: .public) -> \(String(describing: after), privacy: .public)")
            if after == .on, before != .on, !isLaunchdInstance {
                // launchd starts its own instance now (RunAtLoad); hand over so one icon remains.
                livenessLog.info("login item registered from a hand-opened instance; handing over to launchd in 2 s")
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { exit(0) }
            }
        }
    }

    /// Optional same-user local triggers: SIGUSR1 opens Google sign-in,
    /// and SIGUSR2 refreshes the inbox.
    private var signalSources: [DispatchSourceSignal] = []
    private func installSignalTrigger() {
        for (sig, action) in [(SIGUSR1, #selector(linkGoogle)), (SIGUSR2, #selector(refreshNow))] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { [weak self] in _ = self?.perform(action) }
            src.resume()
            signalSources.append(src)
        }
    }

    // MARK: network

    private func startNetwork() {
        guard let client = client else { return }
        guard client.baseURL != nil else {
            connectionItem.title = "Server: not configured (apiBaseURL)"
            return
        }
        streamTask?.cancel(); pollTask?.cancel()
        networkGeneration += 1
        let generation = networkGeneration
        Task { @MainActor in
            await inboxReads.cancel()
            guard generation == self.networkGeneration else { return }
            self.startNetworkWorkers(generation: generation)
        }
    }

    private func startNetworkWorkers(generation: Int) {
        Task { await checkDevice() }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.poll()
                try? await Task.sleep(nanoseconds: Self.pollInterval * 1_000_000_000)
            }
        }
        streamTask = Task { [weak self] in
            var backoff: UInt64 = 2
            while !Task.isCancelled {
                guard let self = self, let client = self.client else { return }
                var opened = false
                do {
                    let sinceId = self.lastEventId
                    try await client.stream(lastEventId: sinceId, onOpen: {
                        guard generation == self.networkGeneration, !Task.isCancelled else { return }
                        opened = true
                        self.postLog.notice("stream open lastEventId=\(sinceId ?? "none", privacy: .public)")
                        self.streamConnected = true
                        self.liveness.streamConnected = true
                        self.connectionItem.title = "Server: stream connected"
                        Task { await self.poll() }   // catch up on anything missed while reconnecting
                    }, onEvent: { env, id in
                        guard generation == self.networkGeneration, !Task.isCancelled else { return }
                        if let id = id { self.cursor.advance(to: id) }
                        self.handle(event: env)
                    }, onReset: {
                        guard generation == self.networkGeneration, !Task.isCancelled else { return }
                        self.cursor.value = nil
                        Task { await self.poll() }
                    })
                    guard generation == self.networkGeneration, !Task.isCancelled else { return }
                    await MainActor.run { self.streamConnected = false; self.liveness.streamConnected = false; self.connectionItem.title = "Server: stream closed, reconnecting" }
                } catch {
                    guard generation == self.networkGeneration, !Task.isCancelled else { return }
                    await MainActor.run {
                        self.handleAuthorizationFailure(error)
                        self.streamConnected = false
                        self.liveness.streamConnected = false
                        self.connectionItem.title = "Server: stream error, polling (\(error.localizedDescription))"
                    }
                }
                backoff = opened ? 2 : min(backoff * 2, 60)
                try? await Task.sleep(nanoseconds: backoff * 1_000_000_000)
            }
        }
    }

    /// Signed GET /v1/devices/self: proves the key is registered and shows the Google link state.
    private func checkDevice() async {
        guard let client = client else { return }
        let generation = networkGeneration
        do {
            let me = try await client.deviceSelf()
            guard generation == networkGeneration, !Task.isCancelled else { return }
            await MainActor.run {
                let link = me.google_email.map { "Google: \($0)" } ?? "Google: not linked"
                self.deviceItem.title = "Device: \(me.device_id) (\(me.label ?? "?"), \(link))"
                self.showPriority(me.priority)
            }
        } catch {
            guard generation == networkGeneration, !Task.isCancelled else { return }
            handleAuthorizationFailure(error)
            await MainActor.run { self.deviceItem.title = "Device: \(self.signer?.deviceId ?? "?") (server: \(error.localizedDescription))" }
        }
    }

    private let inboxReads = SingleFlight<[InboxItem]>()
    @discardableResult
    private func poll() async -> Bool {
        guard let client = client else { return false }
        let generation = networkGeneration
        do {
            let items = try await inboxReads.value { try await client.inbox() }
            try Task.checkCancellation()
            guard generation == networkGeneration else { return false }
            recordRead()
            apply(store.applyInbox(items))
            repostCleared()
            if !streamConnected { connectionItem.title = "Server: polling every \(Self.pollInterval)s" }
            return true
        } catch {
            guard generation == networkGeneration else { return false }
            handleAuthorizationFailure(error)
            if !streamConnected { connectionItem.title = "Server: poll error (\(error.localizedDescription))" }
            return false
        }
    }

    private func handleAuthorizationFailure(_ error: Error) {
        if case APIClient.ClientError.http(let code, _) = error, code == 401 || code == 403 {
            networkGeneration += 1
            streamTask?.cancel()
            streamConnected = false; liveness.streamConnected = false
            cursor.value = nil
            apply(store.applyInbox([]))
            answerKeys.removeAll()
            askCategories.removeAll()
            UNUserNotificationCenter.current().setNotificationCategories(Self.baseCategories())
            setLastAction("access refused; local asks and notifications cleared")
        }
    }

    private func handle(event: StreamEnvelope) {
        if event.event == "stream.reset" {
            cursor.value = nil
            Task { await self.poll() }
            return
        }
        recordRead()
        // HCA-P14: created and escalated envelopes name the devices they are
        // for; ignore the ones not addressed to this device.
        apply(store.apply(event: event, deviceId: signer?.deviceId))
    }

    /// HCA-P14: re-post pending asks whose banner the owner cleared (or that
    /// never showed) once per repost window; runs after every poll.
    static let repostWindow: TimeInterval = 90
    private func repostCleared() {
        let center = UNUserNotificationCenter.current()
        center.getDeliveredNotifications { delivered in
            let ids = Set(delivered.map { $0.request.identifier })
            DispatchQueue.main.async {
                let stillShown = Set(self.store.pending.keys.filter { ids.contains(NotificationShaper.notificationIdentifier(for: $0)) })
                let due = self.store.repostDue(delivered: stillShown, shownAt: self.shownAt, now: Date(), window: Self.repostWindow)
                for item in due { self.post(item: item, reason: "repost") }
            }
        }
    }

    private func apply(_ delta: InboxStore.Delta) {
        for item in delta.added { post(item: item, reason: "added") }
        if !delta.removed.isEmpty {
            UNUserNotificationCenter.current().removeDeliveredNotifications(
                withIdentifiers: delta.removed.map { NotificationShaper.notificationIdentifier(for: $0) })
            UNUserNotificationCenter.current().removePendingNotificationRequests(
                withIdentifiers: delta.removed.map { NotificationShaper.notificationIdentifier(for: $0) })
            for id in delta.removed {
                shownAt.removeValue(forKey: id); answerKeys.removeValue(forKey: id)
                askCategories.removeValue(forKey: NotificationShaper.categoryPrefix + "ask." + id)
            }
            UNUserNotificationCenter.current().setNotificationCategories(Self.baseCategories().union(askCategories.values))
        }
        pendingItem.title = "Pending asks (\(store.count))"
        rebuildAskRows()
    }

    /// HCA-P15: the rows under "Pending asks": each opens a submenu with the
    /// why line and one line per option; a click sends the same signed answer
    /// the banner sends, path [macos_notification, menu, <option_id>].
    private func rebuildAskRows() {
        guard let menu = menu else { return }
        for item in askRowItems { menu.removeItem(item) }
        askRowItems.removeAll()
        let rows = MenuAskList.rows(from: store.pending)
        noAsksItem.isHidden = !rows.isEmpty
        var index = menu.index(of: noAsksItem) + 1
        for row in rows {
            let parent = NSMenuItem(title: "\(row.title)  [\(MenuAskList.shortId(row.requestId))]", action: nil, keyEquivalent: "")
            let sub = NSMenu(title: row.title)
            sub.autoenablesItems = false
            parent.toolTip = store.pending[row.requestId]?.title
            let details = NSMenuItem(title: "Review full decision details…", action: #selector(reviewDetails(_:)), keyEquivalent: "")
            details.target = self; details.representedObject = row.requestId; details.isEnabled = true
            sub.addItem(details)
            sub.addItem(.separator())
            if let why = row.why {
                let whyItem = NSMenuItem(title: why, action: nil, keyEquivalent: "")
                whyItem.isEnabled = false
                sub.addItem(whyItem)
                sub.addItem(.separator())
            }
            if row.answerable {
                for choice in row.choices {
                    let c = NSMenuItem(title: choice.title, action: #selector(chooseAnswer(_:)), keyEquivalent: "")
                    c.target = self
                    c.isEnabled = !submitting.contains(row.requestId)
                    c.toolTip = store.pending[row.requestId]?.options_or_fields.first { $0.id == choice.optionId }?.label
                    c.state = choice.suggested ? .on : .off
                    c.representedObject = [row.requestId, choice.optionId]
                    sub.addItem(c)
                }
            } else {
                let t = NSMenuItem(title: "Needs typed input: answer in Chrome", action: nil, keyEquivalent: "")
                t.isEnabled = false
                sub.addItem(t)
            }
            parent.submenu = sub
            menu.insertItem(parent, at: index)
            askRowItems.append(parent)
            index += 1
        }
    }

    @objc func chooseAnswer(_ sender: NSMenuItem) {
        guard let pair = sender.representedObject as? [String], pair.count == 2 else { return }
        let requestId = pair[0], optionId = pair[1]
        Task { @MainActor in
            guard await self.poll(), let item = self.store.pending[requestId],
                  item.options_or_fields.contains(where: { $0.id == optionId }) else {
                self.setLastAction("ask unavailable; refresh or use Chrome")
                return
            }
            guard self.confirm(item: item, optionId: optionId) else { return }
            let body = AnswerSubmission(control: item.control, value: optionId,
                                        path_taken: ["macos_notification", "menu", optionId])
            await self.submit(requestId: requestId, body: body)
        }
    }

    private func decisionAlert(item: InboxItem) -> NSAlert {
        let alert = NSAlert()
        alert.messageText = item.isRedGate ? "Review red-gate decision" : "Review decision"
        alert.alertStyle = item.isRedGate ? .warning : .informational
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 620, height: 340))
        scroll.hasVerticalScroller = true
        let text = NSTextView(frame: scroll.bounds)
        text.isEditable = false; text.isSelectable = true
        text.font = .systemFont(ofSize: 14)
        text.string = item.decisionContext
        text.textContainer?.widthTracksTextView = true
        text.isVerticallyResizable = true
        text.setAccessibilityLabel("Full decision context")
        scroll.documentView = text
        alert.accessoryView = scroll
        return alert
    }

    private func confirm(item: InboxItem, optionId: String) -> Bool {
        guard !submitting.contains(item.request_id),
              let option = item.options_or_fields.first(where: { $0.id == optionId }) else { return false }
        let alert = decisionAlert(item: item)
        alert.informativeText = "Selected answer: " + option.label + "\nReview the full context before submitting."
        alert.addButton(withTitle: "Cancel")
        alert.addButton(withTitle: "Submit selected answer")
        NSApp.activate(ignoringOtherApps: true)
        return alert.runModal() == .alertSecondButtonReturn
    }

    private func review(item: InboxItem) {
        let alert = decisionAlert(item: item)
        alert.informativeText = MenuAskList.menuControls.contains(item.control)
            ? "Choose an answer from this ask's menu after reviewing these details."
            : "Open the Human Collections Agent Chrome card to answer this control."
        alert.addButton(withTitle: "Close")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    @objc private func reviewDetails(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String, let item = store.pending[id] else { return }
        review(item: item)
    }

    /// HCA-P15: every owner action and its outcome goes to the menu line and
    /// to the unified log (subsystem com.alternativespaces.hca.notifier,
    /// category actions) so a proof can be read with `log show` instead of
    /// asking the owner to read the menu.
    private let actionLog = Logger(subsystem: "com.alternativespaces.hca.notifier", category: "actions")
    private func setLastAction(_ text: String) {
        lastActionItem.title = "Last action: " + text
        actionLog.notice("\(text, privacy: .public)")
    }

    private var linking = false
    private var linkTask: Task<Void, Never>?

    /// Day 4: installed-app sign-in, then the device-signed link call.
    @objc func linkGoogle() {
        NSLog("hca.link menu action (linking=%@, client=%@)", linking ? "yes" : "no", client == nil ? "nil" : "set")
        guard !linking, let client = client else { return }
        linking = true
        setLastAction("Google sign-in opened in the browser")
        linkTask = Task { @MainActor in
            defer { if self.client === client { self.linking = false; self.linkTask = nil } }
            do {
                let token = try await GoogleLinker().obtainAccessToken(loginHint: nil)
                guard self.client === client, !Task.isCancelled else { return }
                let me = try await client.googleLink(accessToken: token)
                guard self.client === client, !Task.isCancelled else { return }
                await MainActor.run {
                    self.deviceItem.title = "Device: \(me.device_id) (\(me.label ?? "?"), Google: \(me.google_email ?? "?"))"
                    self.setLastAction("Google account linked")
                    self.startNetwork()
                }
                await self.poll()
            } catch {
                guard self.client === client, !Task.isCancelled else { return }
                NSLog("hca.link failed: %@", error.localizedDescription)
                await MainActor.run {
                    self.setLastAction("Google link failed")
                    self.postLocal(title: "HCA: Google link failed", body: error.localizedDescription)
                }
            }
        }
    }

    @objc func refreshNow() {
        Task { await poll() }
    }

    // MARK: surface priority (HCA-P14 priority controls)

    private func showPriority(_ priority: Int?) {
        priorityItem.title = SurfacePriority.label(for: priority)
        for item in priorityChoiceItems { item.state = (item.tag == priority) ? .on : .off }
    }

    @objc func choosePriority(_ sender: NSMenuItem) {
        let value = sender.tag
        setLastAction("priority click (\(value))")
        guard let client = client else {
            priorityItem.title = "Priority: not saved (server not configured)"
            return
        }
        priorityItem.title = "Priority: saving (\(value))"
        Task {
            do {
                let me = try await client.setPriority(value)
                guard self.client === client, !Task.isCancelled else { return }
                await MainActor.run {
                    self.showPriority(me.priority)
                    self.setLastAction("priority set to \(value)")
                }
            } catch {
                guard self.client === client, !Task.isCancelled else { return }
                self.handleAuthorizationFailure(error)
                await MainActor.run {
                    self.priorityItem.title = "Priority: not saved (\(error.localizedDescription))"
                    self.setLastAction("priority change refused")
                }
            }
        }
    }

    // MARK: notifications

    static func categoryIdentifier(for shape: NotificationShape) -> String {
        switch shape {
        case .approveDeny: return NotificationShaper.categoryPrefix + "approve_deny"
        case .radio(let options): return NotificationShaper.categoryPrefix + "radio\(options.count)"
        case .alertOnly: return NotificationShaper.categoryPrefix + "alert"
        }
    }

    /// Fixed categories: approve_deny, placeholder radio sizes, alert-only.
    static func baseCategories() -> Set<UNNotificationCategory> {
        var set = Set<UNNotificationCategory>()
        set.insert(UNNotificationCategory(
            identifier: NotificationShaper.categoryPrefix + "approve_deny",
            actions: [
                UNNotificationAction(identifier: "approve", title: "Approve", options: [.authenticationRequired]),
                UNNotificationAction(identifier: "deny", title: "Deny", options: [.destructive, .authenticationRequired]),
            ],
            intentIdentifiers: [], options: [.customDismissAction]))
        for n in 1...NotificationShape.maxRadioOptions {
            let actions = (0..<n).map { i in
                UNNotificationAction(identifier: "option_\(i)", title: "Option \(i + 1)", options: [.authenticationRequired])
            }
            set.insert(UNNotificationCategory(identifier: NotificationShaper.categoryPrefix + "radio\(n)",
                                              actions: actions, intentIdentifiers: [], options: [.customDismissAction]))
        }
        set.insert(UNNotificationCategory(identifier: NotificationShaper.categoryPrefix + "alert",
                                          actions: [], intentIdentifiers: [], options: []))
        return set
    }

    /// Per-ask category so the buttons carry the ask's own labels.
    private var askCategories: [String: UNNotificationCategory] = [:]

    private func categoryFor(item: InboxItem, shape: NotificationShape) -> String {
        let actions: [UNNotificationAction]
        switch shape {
        case .approveDeny:
            actions = ActionMapper.notificationActions(for: item).map { action in
                UNNotificationAction(identifier: action.identifier, title: action.title,
                    options: action.identifier == "deny" ? [.destructive, .authenticationRequired] : [.authenticationRequired])
            }
        case .radio(let options):
            actions = options.map { UNNotificationAction(identifier: $0.identifier, title: $0.title, options: [.authenticationRequired]) }
        case .alertOnly:
            return NotificationShaper.categoryPrefix + "alert"
        }
        let id = NotificationShaper.categoryPrefix + "ask." + item.request_id
        guard askCategories[id] != nil || askCategories.count < 64 else { return NotificationShaper.categoryPrefix + "alert" }
        askCategories[id] = UNNotificationCategory(identifier: id, actions: actions, intentIdentifiers: [], options: [.customDismissAction])
        UNUserNotificationCenter.current().setNotificationCategories(Self.baseCategories().union(askCategories.values))
        return id
    }

    private func post(item: InboxItem, reason: String) {
        postLog.notice("post \(item.request_id, privacy: .public) \(reason, privacy: .public)")
        // Red-gate decisions always open the complete review context first.
        let redact = redactNotifications
        let shape = NotificationShaper.shape(for: item, redacted: redact)
        let wanted = categoryFor(item: item, shape: shape)
        let origin = client?.baseURL?.absoluteString ?? ""
        shownAt[item.request_id] = Date()
        Task { @MainActor in
            let installed: Bool = await withCheckedContinuation { continuation in
                UNUserNotificationCenter.current().getNotificationCategories { categories in
                    continuation.resume(returning: categories.contains { $0.identifier == wanted })
                }
            }
            guard self.client?.baseURL?.absoluteString ?? "" == origin, self.redactNotifications == redact,
                  item.request_id.hasPrefix("test-") || self.store.pending[item.request_id] != nil else { return }
            let content = UNMutableNotificationContent()
            content.title = redact ? "HCA: decision waiting" : (item.isRedGate ? "RED GATE: " : "") + item.title
            var body = NotificationShaper.body(for: item.summary, shape: shape)
            if item.isRedGate { body = "Open HCA's menu and review the full decision details before answering." }
            if let why = item.why, !why.isEmpty { body = why + (body.isEmpty ? "" : "\n") + body }
            content.body = redact ? "Open HCA's menu to review and answer this decision." : body
            content.subtitle = redact ? "" : item.caller.map { "from \($0)" } ?? ""
            content.categoryIdentifier = installed ? wanted : NotificationShaper.categoryPrefix + "alert"
            content.sound = .default
            content.interruptionLevel = item.isRedGate ? .timeSensitive : .active
            content.userInfo = ["request_id": item.request_id, "api_origin": origin,
                                "option_ids": item.options_or_fields.map(\.id)]
            let request = UNNotificationRequest(identifier: NotificationShaper.notificationIdentifier(for: item.request_id),
                                                content: content, trigger: nil)
            do { try await UNUserNotificationCenter.current().add(request) }
            catch { self.setLastAction("post failed \(error.localizedDescription)") }
        }
    }

    private func postLocal(title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.categoryIdentifier = NotificationShaper.categoryPrefix + "alert"
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: "hca-local-\(UUID().uuidString)", content: content, trigger: nil))
    }

    // MARK: test notifications (no server)

    private func postTest(_ ask: AskSummary) {
        let item = InboxItem(request_id: ask.requestId, caller: "test", title: ask.title, control: ask.control,
                             options_or_fields: ask.options.map { InboxItem.Option(id: $0.value, label: $0.label) },
                             suggested: nil)
        post(item: item, reason: "test")
    }

    @objc func sendTestApproveDeny() {
        postTest(AskSummary(requestId: "test-approve-\(Int(Date().timeIntervalSince1970))", control: "approve_deny",
                            title: "HCA test: approve this test?", options: [(value: "approve", label: "Approve"), (value: "deny", label: "Deny")], suggestedValue: nil))
    }

    @objc func sendTestRadio() {
        postTest(AskSummary(requestId: "test-radio-\(Int(Date().timeIntervalSince1970))", control: "radio",
                            title: "HCA test: pick one of three",
                            options: [(value: "a", label: "Alpha"), (value: "b", label: "Beta"), (value: "c", label: "Gamma")],
                            suggestedValue: "b"))
    }

    // MARK: device key

    static var publicKeyFileURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("HCA Notifier", isDirectory: true)
        return dir.appendingPathComponent("device_public_key.pem")
    }

    static func writePublicKeyFile(_ signer: DeviceSigner) {
        let url = publicKeyFileURL
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? signer.publicKeyPEM.write(to: url, atomically: true, encoding: .utf8)
    }

    @objc func copyPublicKey() {
        guard let signer = signer else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(signer.publicKeyPEM, forType: .string)
        setLastAction("public key PEM copied")
    }

    @objc func copyDeviceId() {
        guard let signer = signer else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(signer.deviceId, forType: .string)
        setLastAction("device id copied")
    }

    // MARK: action taps

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let requestId = NotificationShaper.requestId(fromNotificationIdentifier: response.notification.request.identifier) ?? "?"
        let action = response.actionIdentifier
        let origin = response.notification.request.content.userInfo["api_origin"] as? String
        let shownOptions = response.notification.request.content.userInfo["option_ids"] as? [String]
        // The notification framework owns this completion; acknowledge synchronously.
        completionHandler()
        Task { @MainActor in
            if requestId.hasPrefix("test-") { self.setLastAction("test notification action (not sent)"); return }
            guard origin == self.client?.baseURL?.absoluteString else {
                self.postLocal(title: "HCA: notification is stale", body: "This notification belongs to another API origin. Refresh the inbox.")
                return
            }
            // Recover mappings after process restart from a fresh, authorized inbox.
            // Never trust option metadata persisted in the system notification.
            guard await self.poll() else {
                self.postLocal(title: "HCA: ask is unavailable", body: "The inbox could not be refreshed. No answer was sent.")
                return
            }
            let result = NotificationRecovery.resolve(requestId: requestId, action: action,
                isDefaultAction: action == UNNotificationDefaultActionIdentifier,
                notificationOrigin: origin, apiOrigin: self.client?.baseURL?.absoluteString,
                shownOptionIds: shownOptions, inbox: self.store.pending)
            let item: InboxItem
            let optionId: String
            switch result {
            case .answer(let refreshed, let option): item = refreshed; optionId = option
            case .review(let refreshed): self.review(item: refreshed); return
            case .unavailable, .changedChoices, .wrongOrigin:
                self.postLocal(title: "HCA: notification is stale", body: "The ask or its choices are no longer available. Review the current inbox before answering.")
                return
            case .ignored: return
            }
            guard self.confirm(item: item, optionId: optionId) else { return }
            let body = AnswerSubmission(control: item.control, value: optionId,
                                        path_taken: ["macos_notification", "banner", action])
            await self.submit(requestId: requestId, body: body)
        }
    }

    private func submit(requestId: String, body: AnswerSubmission) async {
        guard let client = client, !submitting.contains(requestId) else { return }
        let generation = networkGeneration
        submitting.insert(requestId)
        defer { submitting.remove(requestId); rebuildAskRows() }
        rebuildAskRows()
        let key: String
        if let saved = answerKeys[requestId], saved.0 == body { key = saved.1 }
        else { key = UUID().uuidString; answerKeys[requestId] = (body, key) }
        do {
            let outcome = try await client.answer(requestId: requestId, body: body, idempotencyKey: key)
            guard generation == networkGeneration else { return }
            await MainActor.run {
                switch outcome {
                case .recorded:
                    self.setLastAction("answered \(body.value) on \(requestId)")
                case .alreadyAnswered:
                    self.setLastAction("\(requestId) was already answered elsewhere")
                case .gone:
                    self.setLastAction("\(requestId) is gone (cancelled or expired)")
                case .refused(let code, let text):
                    self.handleAuthorizationFailure(APIClient.ClientError.http(code, text))
                    self.setLastAction("refused HTTP \(code) on \(requestId)")
                    self.postLocal(title: "HCA: answer refused (HTTP \(code))", body: String(text.prefix(140)))
                    return
                }
                self.store.markAnswered(requestId)
                self.apply(InboxStore.Delta(added: [], removed: [requestId]))
            }
        } catch {
            guard generation == networkGeneration else { return }
            handleAuthorizationFailure(error)
            await MainActor.run {
                self.setLastAction("send failed on \(requestId)")
                self.postLocal(title: "HCA: answer not sent", body: "\(error.localizedDescription). The ask stays pending; answer again or use Chrome.")
            }
        }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}

// Ignore the trigger signals before anything else runs: a SIGUSR1 that arrives
// during launch, before the dispatch sources exist, would otherwise terminate the app.
signal(SIGUSR1, SIG_IGN)
signal(SIGUSR2, SIG_IGN)
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()

extension AppDelegate {
    /// HCA-P18: the liveness age and the login item state are read when the menu opens.
    func menuWillOpen(_ menu: NSMenu) {
        refreshLivenessRow()
        refreshLoginItemRow()
    }
}
