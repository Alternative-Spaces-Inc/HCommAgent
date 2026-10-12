import Foundation

/// One pending ask as served by GET /v1/inbox (CollectionRequest plus request_id)
/// and carried in request.created stream events.
public struct InboxItem: Decodable, Equatable, Sendable {
    public struct Option: Decodable, Equatable, Sendable {
        public let id: String
        public let label: String
        public let description: String?
        public init(id: String, label: String, description: String? = nil) {
            self.id = id; self.label = label; self.description = description
        }
    }
    public struct Suggested: Decodable, Equatable, Sendable {
        public let option_id: String?
        public let why: String?
    }
    public struct RedGate: Decodable, Equatable, Sendable {
        public let resource: String?
        public let change: String?
        public let impact: String?
        public let exact_command: String?
        public let rollback: String?
        public init(resource: String? = nil, change: String? = nil, impact: String? = nil,
                    exact_command: String? = nil, rollback: String? = nil) {
            self.resource = resource; self.change = change; self.impact = impact
            self.exact_command = exact_command; self.rollback = rollback
        }
    }

    public let request_id: String
    public let caller: String?
    public let title: String
    public let why: String?
    public let control: String
    public let options_or_fields: [Option]
    public let suggested: Suggested?
    public let red_gate: RedGate?
    public let expires_at: String?
    public let deadline_at: String?

    public init(request_id: String, caller: String? = nil, title: String, why: String? = nil, control: String,
                options_or_fields: [Option], suggested: Suggested? = nil, red_gate: RedGate? = nil,
                expires_at: String? = nil, deadline_at: String? = nil) {
        self.request_id = request_id; self.caller = caller; self.title = title; self.why = why; self.control = control
        self.options_or_fields = options_or_fields; self.suggested = suggested; self.red_gate = red_gate; self.expires_at = expires_at
        self.deadline_at = deadline_at
    }

    public var isRedGate: Bool { red_gate != nil }

    /// Complete context shown before a decision; never truncate the material change.
    public var decisionContext: String {
        var lines = [title]
        if let why, !why.isEmpty { lines.append("Why: " + why) }
        if let gate = red_gate {
            for (label, value) in [("Resource", gate.resource), ("Change", gate.change),
                                   ("Impact", gate.impact), ("Exact command", gate.exact_command),
                                   ("Rollback", gate.rollback)] {
                if let value, !value.isEmpty { lines.append(label + ": " + value) }
            }
        }
        for option in options_or_fields {
            lines.append(option.label + (option.description.map { ": " + $0 } ?? ""))
        }
        if let deadline = deadline_at ?? expires_at { lines.append("Deadline: " + deadline) }
        return lines.joined(separator: "\n\n")
    }

    /// Bridge to the Day 1 shaper. approve_deny asks carry their two options
    /// (ids approve and deny) in options_or_fields like every selection.
    public var summary: AskSummary {
        AskSummary(requestId: request_id, control: control, title: title,
                   options: options_or_fields.map { (value: $0.id, label: $0.label) },
                   suggestedValue: suggested?.option_id)
    }
}

/// Envelope of every line pair on GET /v1/inbox/stream:
///   event: request.created | request.answered | request.cancelled | request.expired
///   data: {"event": ..., "data": {...}, "timestamp": ...}
public struct StreamEnvelope: Decodable, Sendable {
    public let event: String
    public let timestamp: String?
    public let data: StreamData

    public struct StreamData: Decodable, Sendable {
        public let request_id: String?
        public let state: String?
        /// Present on request.created and request.escalated: the full item.
        public let item: InboxItem?
        /// HCA-P14 surface routing: device ids the event is addressed to right
        /// now. Nil on an older server, which means everyone.
        public let visible_to: [String]?

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: Keys.self)
            request_id = try c.decodeIfPresent(String.self, forKey: .request_id)
            state = try c.decodeIfPresent(String.self, forKey: .state)
            visible_to = try c.decodeIfPresent([String].self, forKey: .visible_to)
            // request.created carries CollectionRequest fields at the same level.
            item = try? InboxItem(from: decoder)
        }
        enum Keys: String, CodingKey { case request_id, state, visible_to }
    }
}

/// Body of POST /v1/requests/{id}/answer from this surface.
public struct AnswerSubmission: Encodable, Equatable, Sendable {
    public let control: String
    public let value: String
    public let surface = "macos_notification"
    public let path_taken: [String]
    public let duration_ms: Int?

    public init(control: String, value: String, path_taken: [String], duration_ms: Int? = nil) {
        self.control = control; self.value = value; self.path_taken = path_taken; self.duration_ms = duration_ms
    }

    /// Stable key order so the signed body hash is reproducible in tests.
    public func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(self)
    }
}

/// Maps a tapped notification action back to the option id to send.
public enum ActionMapper {
    /// A button's label and value must use the same mapping, including reordered
    /// approve/deny options and older asks with custom yes/no option IDs.
    public static func notificationActions(for item: InboxItem) -> [NotificationAction] {
        switch NotificationShaper.shape(for: item.summary) {
        case .approveDeny:
            return ["approve", "deny"].compactMap { action in
                guard let value = optionId(for: action, item: item),
                      let option = item.options_or_fields.first(where: { $0.id == value }) else { return nil }
                return NotificationAction(identifier: action, title: option.label, value: value)
            }
        case .radio(let actions): return actions
        case .alertOnly: return []
        }
    }

    /// Returns nil for dismiss, default tap and unknown actions.
    public static func optionId(for actionIdentifier: String, item: InboxItem) -> String? {
        let shape = NotificationShaper.shape(for: item.summary)
        switch shape {
        case .approveDeny:
            if actionIdentifier == "approve" || actionIdentifier == "deny" {
                let ids = item.options_or_fields.map { $0.id }
                // Canonical approve/deny IDs can arrive in either order. Custom
                // IDs use the displayed order; never mix the two strategies.
                if ids.contains("approve"), ids.contains("deny") { return actionIdentifier }
                if actionIdentifier == "approve", let first = ids.first { return first }
                if actionIdentifier == "deny", ids.count > 1 { return ids[1] }
                return nil
            }
            return nil
        case .radio(let actions):
            return actions.first { $0.identifier == actionIdentifier }?.value
        case .alertOnly:
            return nil
        }
    }
}
