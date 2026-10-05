import Foundation

/// Pending asks by id, with dedupe between the stream and the poll. Pure
/// state; the app posts and removes notifications from the deltas.
public struct InboxStore: Equatable {
    public private(set) var pending: [String: InboxItem] = [:]
    /// Ids already shown as a notification, so a reconnect or poll does not re-post.
    public private(set) var shown: Set<String> = []

    public init() {}

    public struct Delta: Equatable {
        public var added: [InboxItem]
        public var removed: [String]
        public init(added: [InboxItem] = [], removed: [String] = []) {
            self.added = added; self.removed = removed
        }
    }

    /// Replace the pending set with a full inbox read.
    public mutating func applyInbox(_ items: [InboxItem]) -> Delta {
        var delta = Delta()
        let ids = Set(items.map { $0.request_id })
        for id in pending.keys where !ids.contains(id) {
            pending.removeValue(forKey: id); shown.remove(id); delta.removed.append(id)
        }
        for item in items where pending[item.request_id] == nil {
            pending[item.request_id] = item
            if !shown.contains(item.request_id) { delta.added.append(item); shown.insert(item.request_id) }
        }
        delta.removed.sort()
        return delta
    }

    /// HCA-P14: a created or escalated envelope that carries visible_to is
    /// shown only when it names this device. Without the field (older
    /// server) or without a known device id the envelope is shown.
    public static func isAddressed(_ event: StreamEnvelope, to deviceId: String?) -> Bool {
        guard let list = event.data.visible_to else { return true }
        guard let id = deviceId else { return false }
        return list.contains(id)
    }

    public mutating func apply(event: StreamEnvelope, deviceId: String? = nil) -> Delta {
        var delta = Delta()
        switch event.event {
        case "request.created", "request.escalated":
            if event.data.visible_to != nil, !Self.isAddressed(event, to: deviceId) { break }
            if let item = event.data.item, pending[item.request_id] == nil {
                pending[item.request_id] = item
                if !shown.contains(item.request_id) { delta.added.append(item); shown.insert(item.request_id) }
            }
        case "request.answered", "request.cancelled", "request.expired":
            if let id = event.data.request_id, pending.removeValue(forKey: id) != nil {
                shown.remove(id); delta.removed.append(id)
            }
        default: break
        }
        return delta
    }

    /// After this device answered: drop locally without waiting for the event.
    public mutating func markAnswered(_ id: String) {
        pending.removeValue(forKey: id); shown.remove(id)
    }

    public var count: Int { pending.count }

    /// HCA-P14 (P13 Day 5 finding 3): a banner the owner cleared was never
    /// shown again until restart. Pending items whose notification is no
    /// longer delivered and whose last post is older than `window` are due.
    public func repostDue(delivered: Set<String>, shownAt: [String: Date], now: Date, window: TimeInterval) -> [InboxItem] {
        pending.values.filter { item in
            guard !delivered.contains(item.request_id) else { return false }
            guard let at = shownAt[item.request_id] else { return true }
            return now.timeIntervalSince(at) >= window
        }.sorted { $0.request_id < $1.request_id }
    }
}
