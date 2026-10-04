import Foundation

/// How an ask is rendered as a macOS notification. Decided from the control
/// type and option count only, so it is testable without UserNotifications.
public enum NotificationShape: Equatable {
    /// Two action buttons, Approve and Deny.
    case approveDeny
    /// One action button per option, in order. At most `maxRadioOptions`.
    case radio(options: [NotificationAction])
    /// No actions. The body tells the owner to answer in Chrome.
    case alertOnly(reason: String)

    public static let maxRadioOptions = 4
}

public struct NotificationAction: Equatable {
    /// Stable identifier sent back as `path_taken` and used to pick the answer value.
    public let identifier: String
    public let title: String
    public let value: String

    public init(identifier: String, title: String, value: String) {
        self.identifier = identifier
        self.title = title
        self.value = value
    }
}

/// Minimal view of a pending ask as served by GET /v1/inbox.
public struct AskSummary: Equatable {
    public let requestId: String
    public let control: String
    public let title: String
    public let options: [(value: String, label: String)]
    public let suggestedValue: String?

    public init(requestId: String, control: String, title: String,
                options: [(value: String, label: String)], suggestedValue: String?) {
        self.requestId = requestId
        self.control = control
        self.title = title
        self.options = options
        self.suggestedValue = suggestedValue
    }

    public static func == (lhs: AskSummary, rhs: AskSummary) -> Bool {
        lhs.requestId == rhs.requestId && lhs.control == rhs.control && lhs.title == rhs.title
            && lhs.suggestedValue == rhs.suggestedValue
            && lhs.options.map { $0.value } == rhs.options.map { $0.value }
            && lhs.options.map { $0.label } == rhs.options.map { $0.label }
    }
}

public enum NotificationShaper {
    /// Category identifier prefix; the full id is prefix plus the shape name.
    public static let categoryPrefix = "hca."
    /// Notification identifier prefix; the request id follows.
    public static let notificationPrefix = "hca-"

    public static func shape(for ask: AskSummary) -> NotificationShape {
        switch ask.control {
        case "approve_deny":
            return .approveDeny
        case "radio":
            if ask.options.isEmpty {
                return .alertOnly(reason: "no options")
            }
            if ask.options.count > NotificationShape.maxRadioOptions {
                return .alertOnly(reason: "\(ask.options.count) options, more than \(NotificationShape.maxRadioOptions)")
            }
            let actions = ask.options.enumerated().map { index, option in
                NotificationAction(identifier: "option_\(index)", title: option.label, value: option.value)
            }
            return .radio(options: actions)
        default:
            return .alertOnly(reason: "control \(ask.control) needs the browser card")
        }
    }

    /// Body text: the title, a marker for the suggested option, and the fallback hint.
    public static func body(for ask: AskSummary, shape: NotificationShape) -> String {
        var lines: [String] = []
        if let suggested = ask.suggestedValue,
           let option = ask.options.first(where: { $0.value == suggested }) {
            lines.append("Suggested: \(option.label)")
        }
        if case .alertOnly = shape {
            lines.append("Answer in Chrome (Human Collections Agent card).")
        }
        return lines.joined(separator: "\n")
    }

    public static func notificationIdentifier(for requestId: String) -> String {
        notificationPrefix + requestId
    }

    public static func requestId(fromNotificationIdentifier id: String) -> String? {
        guard id.hasPrefix(notificationPrefix) else { return nil }
        return String(id.dropFirst(notificationPrefix.count))
    }
}
