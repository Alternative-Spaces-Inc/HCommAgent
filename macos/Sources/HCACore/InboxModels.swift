import Foundation

/// One pending ask as served by GET /v1/inbox (CollectionRequest plus request_id)
/// and carried in request.created stream events.
public struct InboxItem: Decodable, Equatable {
    public struct Option: Decodable, Equatable {
        public let id: String
        public let label: String
        public let description: String?
        public init(id: String, label: String, description: String? = nil) {
            self.id = id; self.label = label; self.description = description
        }
    }
    public struct Suggested: Decodable, Equatable {
        public let option_id: String?
        public let why: String?
    }
    public struct RedGate: Decodable, Equatable {
        public let resource: String?
        public let change: String?
        public let impact: String?
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

    public init(request_id: String, caller: String? = nil, title: String, why: String? = nil, control: String,
                options_or_fields: [Option], suggested: Suggested? = nil, red_gate: RedGate? = nil, expires_at: String? = nil) {
        self.request_id = request_id; self.caller = caller; self.title = title; self.why = why; self.control = control
        self.options_or_fields = options_or_fields; self.suggested = suggested; self.red_gate = red_gate; self.expires_at = expires_at
    }

    public var isRedGate: Bool { red_gate != nil }

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
public struct StreamEnvelope: Decodable {
    public let event: String
    public let timestamp: String?
    public let data: StreamData

    public struct StreamData: Decodable {
        public let request_id: String?
        public let state: String?
        /// Present on request.created: the full item.
        public let item: InboxItem?

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: Keys.self)
            request_id = try c.decodeIfPresent(String.self, forKey: .request_id)
            state = try c.decodeIfPresent(String.self, forKey: .state)
            // request.created carries CollectionRequest fields at the same level.
            item = try? InboxItem(from: decoder)
        }
        enum Keys: String, CodingKey { case request_id, state }
    }
}

/// Body of POST /v1/requests/{id}/answer from this surface.
public struct AnswerSubmission: Encodable, Equatable {
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
    /// Returns nil for dismiss, default tap and unknown actions.
    public static func optionId(for actionIdentifier: String, item: InboxItem) -> String? {
        let shape = NotificationShaper.shape(for: item.summary)
        switch shape {
        case .approveDeny:
            if actionIdentifier == "approve" || actionIdentifier == "deny" {
                // Prefer the ask's own option ids when it names them.
                let ids = item.options_or_fields.map { $0.id }
                if ids.contains(actionIdentifier) { return actionIdentifier }
                if actionIdentifier == "approve", let first = ids.first { return first }
                if actionIdentifier == "deny", ids.count > 1 { return ids[1] }
                return actionIdentifier
            }
            return nil
        case .radio(let actions):
            return actions.first { $0.identifier == actionIdentifier }?.value
        case .alertOnly:
            return nil
        }
    }
}
