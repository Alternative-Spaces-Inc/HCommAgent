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

    public mutating func apply(event: StreamEnvelope) -> Delta {
        var delta = Delta()
        switch event.event {
        case "request.created":
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
}
