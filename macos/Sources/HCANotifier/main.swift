import AppKit
import UserNotifications
import HCACore

/// HCA Notifier, Day 1: menu bar item, notification permission, a test
/// notification using the same category shapes the real asks will use.
/// No network yet. Must run from the .app bundle built by scripts/bundle.sh.
final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    private var statusItem: NSStatusItem!
    private let statusMenuItem = NSMenuItem(title: "Status: starting", action: nil, keyEquivalent: "")
    private let lastActionItem = NSMenuItem(title: "Last action: none", action: nil, keyEquivalent: "")

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "checkmark.bubble", accessibilityDescription: "HCA")
            button.toolTip = "Human Collections Agent"
        }
        let menu = NSMenu()
        menu.addItem(statusMenuItem)
        menu.addItem(lastActionItem)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Send test notification (Approve / Deny)", action: #selector(sendTestApproveDeny), keyEquivalent: "t"))
        menu.addItem(NSMenuItem(title: "Send test notification (3 options)", action: #selector(sendTestRadio), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit HCA Notifier", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu

        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.setNotificationCategories(Self.categories())
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
    }

    // One category per shape. Radio categories are keyed by option count so
    // the action buttons can carry the real option labels at post time.
    static func categoryIdentifier(for shape: NotificationShape) -> String {
        switch shape {
        case .approveDeny: return NotificationShaper.categoryPrefix + "approve_deny"
        case .radio(let options): return NotificationShaper.categoryPrefix + "radio\(options.count)"
        case .alertOnly: return NotificationShaper.categoryPrefix + "alert"
        }
    }

    static func categories() -> Set<UNNotificationCategory> {
        var set = Set<UNNotificationCategory>()
        set.insert(UNNotificationCategory(
            identifier: NotificationShaper.categoryPrefix + "approve_deny",
            actions: [
                UNNotificationAction(identifier: "approve", title: "Approve", options: [.authenticationRequired]),
                UNNotificationAction(identifier: "deny", title: "Deny", options: [.destructive, .authenticationRequired]),
            ],
            intentIdentifiers: [], options: [.customDismissAction]))
        for n in 1...NotificationShape.maxRadioOptions {
            // Titles are placeholders: macOS shows the category's titles, so
            // Day 3 registers per-ask categories with the real labels. Day 1
            // proves the plumbing.
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

    private func post(ask: AskSummary) {
        let shape = NotificationShaper.shape(for: ask)
        let content = UNMutableNotificationContent()
        content.title = ask.title
        content.body = NotificationShaper.body(for: ask, shape: shape)
        content.categoryIdentifier = Self.categoryIdentifier(for: shape)
        content.sound = .default
        content.userInfo = ["request_id": ask.requestId]
        let request = UNNotificationRequest(identifier: NotificationShaper.notificationIdentifier(for: ask.requestId),
                                            content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { error in
            DispatchQueue.main.async {
                if let error = error {
                    self.lastActionItem.title = "Last action: post failed \(error.localizedDescription)"
                } else {
                    self.lastActionItem.title = "Last action: posted \(ask.requestId)"
                }
            }
        }
    }

    @objc func sendTestApproveDeny() {
        post(ask: AskSummary(requestId: "test-approve-\(Int(Date().timeIntervalSince1970))", control: "approve_deny",
                             title: "HCA test: approve this test?", options: [], suggestedValue: nil))
    }

    @objc func sendTestRadio() {
        post(ask: AskSummary(requestId: "test-radio-\(Int(Date().timeIntervalSince1970))", control: "radio",
                             title: "HCA test: pick one of three",
                             options: [(value: "a", label: "Alpha"), (value: "b", label: "Beta"), (value: "c", label: "Gamma")],
                             suggestedValue: "b"))
    }

    // Action taps land here. Day 1 only records them in the menu.
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let requestId = NotificationShaper.requestId(fromNotificationIdentifier: response.notification.request.identifier) ?? "?"
        DispatchQueue.main.async {
            self.lastActionItem.title = "Last action: \(response.actionIdentifier) on \(requestId)"
        }
        completionHandler()
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
