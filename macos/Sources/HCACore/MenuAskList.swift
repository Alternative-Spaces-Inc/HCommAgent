import Foundation

/// HCA-P15: pending asks as menu rows. Pure state so the ordering, the red
/// gate prefix, the truncation and the suggested marker are unit tested
/// without AppKit. Red gates come first, then by request id.
public struct MenuAskRow: Equatable, Sendable {
    public struct Choice: Equatable, Sendable {
        public let optionId: String
        public let title: String
        public let suggested: Bool
    }
    public let requestId: String
    public let title: String
    public let why: String?
    public let isRedGate: Bool
    /// Empty when the control cannot be answered from a menu (free text).
    public let choices: [Choice]
    public var answerable: Bool { !choices.isEmpty }
}

public enum MenuAskList {
    public static let titleLimit = 70
    public static let menuControls: Set<String> = ["approve_deny", "radio"]

    public static func truncate(_ s: String, limit: Int = titleLimit) -> String {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: "\n", with: " ")
        guard t.count > limit else { return t }
        return String(t.prefix(limit - 1)) + "…"
    }

    public static func rows(from pending: [String: InboxItem]) -> [MenuAskRow] {
        let items = pending.values.sorted {
            if $0.isRedGate != $1.isRedGate { return $0.isRedGate }
            return $0.request_id < $1.request_id
        }
        return items.map { row(for: $0) }
    }

    public static func row(for item: InboxItem) -> MenuAskRow {
        let prefix = item.isRedGate ? "RED GATE: " : ""
        let choices: [MenuAskRow.Choice]
        if menuControls.contains(item.control) {
            choices = item.options_or_fields.map { opt in
                let suggested = item.suggested?.option_id == opt.id
                return MenuAskRow.Choice(optionId: opt.id,
                                         title: truncate(opt.label, limit: 60) + (suggested ? " (suggested)" : ""),
                                         suggested: suggested)
            }
        } else {
            choices = []
        }
        return MenuAskRow(requestId: item.request_id,
                          title: truncate(prefix + item.title),
                          why: item.why.map { truncate($0, limit: 90) },
                          isRedGate: item.isRedGate,
                          choices: choices)
    }

    /// Short id shown beside the row so two asks with the same title stay apart.
    public static func shortId(_ requestId: String) -> String { String(requestId.prefix(8)) }
}
