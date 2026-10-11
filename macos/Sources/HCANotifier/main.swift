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
    private let loginItem = NSMenuItem(title: "Launch at login", action: #selector(toggleLoginItem), keyEquivalent: "")
    private var liveness = Liveness()
    private var stalenessTask: Task<Void, Never>?
    private let livenessLog = Logger(subsystem: "com.alternativespaces.hca.notifier", category: "liveness")
    private var priorityChoiceItems: [NSMenuItem] = []

    private var signer: DeviceSigner?
    private var client: APIClient?
    private var store = InboxStore()
    private var shownAt: [String: Date] = [:]
    private var streamTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var streamConnected = false
    /// HCA-P20: last SSE id seen, handed back as Last-Event-ID on reconnect.
    private var lastEventId: String?
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
        menu.addItem(NSMenuItem(title: "Copy device public key (PEM)", action: #selector(copyPublicKey), keyEquivalent: "c"))
        menu.addItem(NSMenuItem(title: "Copy device id", action: #selector(copyDeviceId), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Link Google account (opens browser)", action: #selector(linkGoogle), keyEquivalent: "g"))
        menu.addItem(NSMenuItem(title: "Refresh inbox now", action: #selector(refreshNow), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "Send test notification (Approve / Deny)", action: #selector(sendTestApproveDeny), keyEquivalent: "t"))
        menu.addItem(NSMenuItem(title: "Send test notification (3 options)", action: #selector(sendTestRadio), keyEquivalent: ""))
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
            client = APIClient(signer: s)
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

    /// kill -USR1 <pid> runs the Google link flow (the agent triggers it over the bridge;
    /// the owner only signs in). SIGUSR2 refreshes the inbox.
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
                    try await client.stream(lastEventId: self.lastEventId, onOpen: {
                        opened = true
                        DispatchQueue.main.async {
                            self.streamConnected = true
                            self.liveness.streamConnected = true
                            self.connectionItem.title = "Server: stream connected"
                        }
                        Task { await self.poll() }   // catch up on anything missed while reconnecting
                    }, onEvent: { env, id in
                        DispatchQueue.main.async {
                            if let id = id { self.lastEventId = id }
                            self.handle(event: env)
                        }
                    })
                    await MainActor.run { self.streamConnected = false; self.liveness.streamConnected = false; self.connectionItem.title = "Server: stream closed, reconnecting" }
                } catch {
                    await MainActor.run {
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
        do {
            let me = try await client.deviceSelf()
            await MainActor.run {
                let link = me.google_email.map { "Google: \($0)" } ?? "Google: not linked"
                self.deviceItem.title = "Device: \(me.device_id) (\(me.label ?? "?"), \(link))"
                self.showPriority(me.priority)
            }
        } catch {
            await MainActor.run { self.deviceItem.title = "Device: \(self.signer?.deviceId ?? "?") (server: \(error.localizedDescription))" }
        }
    }

    private func poll() async {
        guard let client = client else { return }
        do {
            let items = try await client.inbox()
            await MainActor.run {
                self.recordRead()
                let delta = self.store.applyInbox(items)
                self.apply(delta)
                self.repostCleared()
                if !self.streamConnected { self.connectionItem.title = "Server: polling every \(Self.pollInterval)s" }
            }
        } catch {
            await MainActor.run {
                if !self.streamConnected { self.connectionItem.title = "Server: poll error (\(error.localizedDescription))" }
            }
        }
    }

    private func handle(event: StreamEnvelope) {
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
                for item in due { self.post(item: item) }
            }
        }
    }

    private func apply(_ delta: InboxStore.Delta) {
        for item in delta.added { post(item: item) }
        if !delta.removed.isEmpty {
            UNUserNotificationCenter.current().removeDeliveredNotifications(
                withIdentifiers: delta.removed.map { NotificationShaper.notificationIdentifier(for: $0) })
            for id in delta.removed { shownAt.removeValue(forKey: id) }
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
                    c.isEnabled = true
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
        setLastAction("menu click \(optionId) on \(requestId)")
        guard let item = store.pending[requestId] else {
            setLastAction("\(requestId) no longer pending")
            return
        }
        let started = shownAt[requestId]
        let body = AnswerSubmission(control: item.control, value: optionId,
                                    path_taken: ["macos_notification", "menu", optionId],
                                    duration_ms: started.map { Int(Date().timeIntervalSince($0) * 1000) })
        setLastAction("sending menu \(optionId) for \(requestId)")
        Task { await self.submit(requestId: requestId, body: body) }
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

    /// Day 4: installed-app sign-in, then the device-signed link call.
    @objc func linkGoogle() {
        NSLog("hca.link menu action (linking=%@, client=%@)", linking ? "yes" : "no", client == nil ? "nil" : "set")
        guard !linking, let client = client else { return }
        linking = true
        setLastAction("Google sign-in opened in the browser")
        Task {
            defer { Task { @MainActor in self.linking = false } }
            do {
                let token = try await GoogleLinker().obtainAccessToken(loginHint: nil)
                let me = try await client.googleLink(accessToken: token)
                await MainActor.run {
                    self.deviceItem.title = "Device: \(me.device_id) (\(me.label ?? "?"), Google: \(me.google_email ?? "?"))"
                    self.setLastAction("Google account linked")
                }
                await self.poll()
            } catch {
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
                await MainActor.run {
                    self.showPriority(me.priority)
                    self.setLastAction("priority set to \(value)")
                }
            } catch {
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
            let labels = item.options_or_fields.map { $0.label }
            actions = [
                UNNotificationAction(identifier: "approve", title: labels.first ?? "Approve", options: [.authenticationRequired]),
                UNNotificationAction(identifier: "deny", title: labels.count > 1 ? labels[1] : "Deny", options: [.destructive, .authenticationRequired]),
            ]
        case .radio(let options):
            actions = options.map { UNNotificationAction(identifier: $0.identifier, title: $0.title, options: [.authenticationRequired]) }
        case .alertOnly:
            return NotificationShaper.categoryPrefix + "alert"
        }
        let id = NotificationShaper.categoryPrefix + "ask." + item.request_id
        askCategories[id] = UNNotificationCategory(identifier: id, actions: actions, intentIdentifiers: [], options: [.customDismissAction])
        UNUserNotificationCenter.current().setNotificationCategories(Self.baseCategories().union(askCategories.values))
        return id
    }

    private func post(item: InboxItem) {
        let summary = item.summary
        let shape = NotificationShaper.shape(for: summary)
        let content = UNMutableNotificationContent()
        content.title = (item.isRedGate ? "RED GATE: " : "") + item.title
        var body = NotificationShaper.body(for: summary, shape: shape)
        if let why = item.why, !why.isEmpty { body = why + (body.isEmpty ? "" : "\n") + body }
        content.body = body
        content.subtitle = item.caller.map { "from \($0)" } ?? ""
        content.categoryIdentifier = categoryFor(item: item, shape: shape)
        content.sound = .default
        content.interruptionLevel = item.isRedGate ? .timeSensitive : .active
        content.userInfo = ["request_id": item.request_id]
        shownAt[item.request_id] = Date()
        // The per-ask category was registered a moment ago; a notification posted
        // before usernoted has applied the new set shows without buttons (seen on
        // the 2026-10-04 proof: banner, no Approve or Deny). Read the set back
        // first: getNotificationCategories completes after the pending set has
        // been applied, so the add below is ordered behind it. If the id is still
        // missing, fall back to a base category so the buttons always exist.
        let center = UNUserNotificationCenter.current()
        let wanted = content.categoryIdentifier
        center.getNotificationCategories { categories in
            if !categories.contains(where: { $0.identifier == wanted }) {
                content.categoryIdentifier = NotificationShaper.categoryPrefix + (shape == .approveDeny ? "approve_deny" : "alert")
            }
            let request = UNNotificationRequest(identifier: NotificationShaper.notificationIdentifier(for: item.request_id),
                                                content: content, trigger: nil)
            center.add(request) { error in
                DispatchQueue.main.async {
                    if let error = error { self.setLastAction("post failed \(error.localizedDescription)") }
                }
            }
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
        post(item: item)
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

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let requestId = NotificationShaper.requestId(fromNotificationIdentifier: response.notification.request.identifier) ?? "?"
        let action = response.actionIdentifier
        DispatchQueue.main.async {
            self.setLastAction("\(action) on \(requestId)")
            guard let item = self.store.pending[requestId] else {
                // Test notifications and already-removed asks end here.
                if requestId.hasPrefix("test-") { self.setLastAction("\(action) on \(requestId) (test, not sent)") }
                completionHandler(); return
            }
            guard let optionId = ActionMapper.optionId(for: action, item: item) else { completionHandler(); return }
            let started = self.shownAt[requestId]
            let body = AnswerSubmission(control: item.control, value: optionId,
                                        path_taken: ["macos_notification", "banner", action],
                                        duration_ms: started.map { Int(Date().timeIntervalSince($0) * 1000) })
            self.setLastAction("sending \(optionId) for \(requestId)")
            Task {
                await self.submit(requestId: requestId, body: body)
                completionHandler()
            }
        }
    }

    private func submit(requestId: String, body: AnswerSubmission) async {
        guard let client = client else { return }
        do {
            let outcome = try await client.answer(requestId: requestId, body: body)
            await MainActor.run {
                switch outcome {
                case .recorded:
                    self.setLastAction("answered \(body.value) on \(requestId)")
                case .alreadyAnswered:
                    self.setLastAction("\(requestId) was already answered elsewhere")
                case .gone:
                    self.setLastAction("\(requestId) is gone (cancelled or expired)")
                case .refused(let code, let text):
                    self.setLastAction("refused HTTP \(code) on \(requestId)")
                    self.postLocal(title: "HCA: answer refused (HTTP \(code))", body: String(text.prefix(140)))
                    return
                }
                self.store.markAnswered(requestId)
                self.apply(InboxStore.Delta(added: [], removed: [requestId]))
            }
        } catch {
            await MainActor.run {
                self.setLastAction("send failed on \(requestId)")
                self.postLocal(title: "HCA: answer not sent", body: "\(error.localizedDescription). The ask stays pending; answer again or use Chrome.")
            }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
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
