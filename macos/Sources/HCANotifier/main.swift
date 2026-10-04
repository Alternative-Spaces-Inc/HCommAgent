import AppKit
import UserNotifications
import HCACore

/// HCA Notifier: menu bar item, native notifications with action buttons for
/// pending asks, signed calls on the device key. Must run from the .app
/// bundle built by scripts/bundle.sh (UNUserNotificationCenter refuses a bare
/// executable).
///
/// Day 1: menu, permission, test banners. Day 2: Keychain device key.
/// Day 3: signed inbox read, SSE stream with poll fallback, signed answer,
/// dedupe between stream and poll.
final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    private var statusItem: NSStatusItem!
    private let statusMenuItem = NSMenuItem(title: "Status: starting", action: nil, keyEquivalent: "")
    private let connectionItem = NSMenuItem(title: "Server: not configured", action: nil, keyEquivalent: "")
    private let pendingItem = NSMenuItem(title: "Pending: 0", action: nil, keyEquivalent: "")
    private let lastActionItem = NSMenuItem(title: "Last action: none", action: nil, keyEquivalent: "")
    private let deviceItem = NSMenuItem(title: "Device: loading", action: nil, keyEquivalent: "")

    private var signer: DeviceSigner?
    private var client: APIClient?
    private var store = InboxStore()
    private var shownAt: [String: Date] = [:]
    private var streamTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var streamConnected = false
    static let pollInterval: UInt64 = 60

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "checkmark.bubble", accessibilityDescription: "HCA")
            button.toolTip = "Human Collections Agent"
        }
        let menu = NSMenu()
        menu.addItem(statusMenuItem)
        menu.addItem(connectionItem)
        menu.addItem(pendingItem)
        menu.addItem(lastActionItem)
        menu.addItem(.separator())
        menu.addItem(deviceItem)
        menu.addItem(NSMenuItem(title: "Copy device public key (PEM)", action: #selector(copyPublicKey), keyEquivalent: "c"))
        menu.addItem(NSMenuItem(title: "Copy device id", action: #selector(copyDeviceId), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Link Google account (opens browser)", action: #selector(linkGoogle), keyEquivalent: "g"))
        menu.addItem(NSMenuItem(title: "Refresh inbox now", action: #selector(refreshNow), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "Send test notification (Approve / Deny)", action: #selector(sendTestApproveDeny), keyEquivalent: "t"))
        menu.addItem(NSMenuItem(title: "Send test notification (3 options)", action: #selector(sendTestRadio), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit HCA Notifier", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu

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
        installSignalTrigger()
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
                    try await client.stream(onOpen: {
                        opened = true
                        DispatchQueue.main.async {
                            self.streamConnected = true
                            self.connectionItem.title = "Server: stream connected"
                        }
                        Task { await self.poll() }   // catch up on anything missed while reconnecting
                    }, onEvent: { env in
                        DispatchQueue.main.async { self.handle(event: env) }
                    })
                    await MainActor.run { self.streamConnected = false; self.connectionItem.title = "Server: stream closed, reconnecting" }
                } catch {
                    await MainActor.run {
                        self.streamConnected = false
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
                let delta = self.store.applyInbox(items)
                self.apply(delta)
                if !self.streamConnected { self.connectionItem.title = "Server: polling every \(Self.pollInterval)s" }
            }
        } catch {
            await MainActor.run {
                if !self.streamConnected { self.connectionItem.title = "Server: poll error (\(error.localizedDescription))" }
            }
        }
    }

    private func handle(event: StreamEnvelope) {
        apply(store.apply(event: event))
    }

    private func apply(_ delta: InboxStore.Delta) {
        for item in delta.added { post(item: item) }
        if !delta.removed.isEmpty {
            UNUserNotificationCenter.current().removeDeliveredNotifications(
                withIdentifiers: delta.removed.map { NotificationShaper.notificationIdentifier(for: $0) })
            for id in delta.removed { shownAt.removeValue(forKey: id) }
        }
        pendingItem.title = "Pending: \(store.count)"
    }

    private var linking = false

    /// Day 4: installed-app sign-in, then the device-signed link call.
    @objc func linkGoogle() {
        NSLog("hca.link menu action (linking=%@, client=%@)", linking ? "yes" : "no", client == nil ? "nil" : "set")
        guard !linking, let client = client else { return }
        linking = true
        lastActionItem.title = "Last action: Google sign-in opened in the browser"
        Task {
            defer { Task { @MainActor in self.linking = false } }
            do {
                let token = try await GoogleLinker().obtainAccessToken(loginHint: nil)
                let me = try await client.googleLink(accessToken: token)
                await MainActor.run {
                    self.deviceItem.title = "Device: \(me.device_id) (\(me.label ?? "?"), Google: \(me.google_email ?? "?"))"
                    self.lastActionItem.title = "Last action: Google account linked"
                }
                await self.poll()
            } catch {
                NSLog("hca.link failed: %@", error.localizedDescription)
                await MainActor.run {
                    self.lastActionItem.title = "Last action: Google link failed"
                    self.postLocal(title: "HCA: Google link failed", body: error.localizedDescription)
                }
            }
        }
    }

    @objc func refreshNow() {
        Task { await poll() }
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
        let request = UNNotificationRequest(identifier: NotificationShaper.notificationIdentifier(for: item.request_id),
                                            content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { error in
            DispatchQueue.main.async {
                if let error = error { self.lastActionItem.title = "Last action: post failed \(error.localizedDescription)" }
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
        lastActionItem.title = "Last action: public key PEM copied"
    }

    @objc func copyDeviceId() {
        guard let signer = signer else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(signer.deviceId, forType: .string)
        lastActionItem.title = "Last action: device id copied"
    }

    // MARK: action taps

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let requestId = NotificationShaper.requestId(fromNotificationIdentifier: response.notification.request.identifier) ?? "?"
        let action = response.actionIdentifier
        DispatchQueue.main.async {
            self.lastActionItem.title = "Last action: \(action) on \(requestId)"
            guard let item = self.store.pending[requestId] else {
                // Test notifications and already-removed asks end here.
                if requestId.hasPrefix("test-") { self.lastActionItem.title = "Last action: \(action) on \(requestId) (test, not sent)" }
                completionHandler(); return
            }
            guard let optionId = ActionMapper.optionId(for: action, item: item) else { completionHandler(); return }
            let started = self.shownAt[requestId]
            let body = AnswerSubmission(control: item.control, value: optionId,
                                        path_taken: ["macos_notification", "banner", action],
                                        duration_ms: started.map { Int(Date().timeIntervalSince($0) * 1000) })
            self.lastActionItem.title = "Last action: sending \(optionId) for \(requestId)"
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
                    self.lastActionItem.title = "Last action: answered \(body.value) on \(requestId)"
                case .alreadyAnswered:
                    self.lastActionItem.title = "Last action: \(requestId) was already answered elsewhere"
                case .gone:
                    self.lastActionItem.title = "Last action: \(requestId) is gone (cancelled or expired)"
                case .refused(let code, let text):
                    self.lastActionItem.title = "Last action: refused HTTP \(code) on \(requestId)"
                    self.postLocal(title: "HCA: answer refused (HTTP \(code))", body: String(text.prefix(140)))
                    return
                }
                self.store.markAnswered(requestId)
                self.apply(InboxStore.Delta(added: [], removed: [requestId]))
            }
        } catch {
            await MainActor.run {
                self.lastActionItem.title = "Last action: send failed on \(requestId)"
                self.postLocal(title: "HCA: answer not sent", body: "\(error.localizedDescription). The ask stays pending; answer again or use Chrome.")
            }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
