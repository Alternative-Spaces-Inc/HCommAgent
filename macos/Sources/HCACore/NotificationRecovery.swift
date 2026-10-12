import Foundation

public enum NotificationRecovery {
    public enum Result: Equatable, Sendable {
        case answer(InboxItem, String)
        case review(InboxItem)
        case unavailable
        case wrongOrigin
        case changedChoices
        case ignored
    }

    /// System notifications survive restart; identity and decision context come from a fresh authorized inbox.
    public static func resolve(requestId: String, action: String, isDefaultAction: Bool,
                               notificationOrigin: String?, apiOrigin: String?, shownOptionIds: [String]?,
                               inbox: [String: InboxItem]) -> Result {
        guard let apiOrigin, notificationOrigin == apiOrigin else { return .wrongOrigin }
        guard let item = inbox[requestId] else { return .unavailable }
        if isDefaultAction { return .review(item) }
        guard shownOptionIds == item.options_or_fields.map(\.id) else { return .changedChoices }
        guard let option = ActionMapper.optionId(for: action, item: item) else { return .ignored }
        return .answer(item, option)
    }
}
