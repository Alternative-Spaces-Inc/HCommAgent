import Foundation

/// HCA-P20 Day 5 follow-up: the last SSE event id this device has seen,
/// kept across process restarts so the first connect after a relaunch sends
/// Last-Event-ID and the server replays what was published while the app was
/// down (the live owner proof on 2026-10-11 showed replays 0: the id lived in
/// memory only, delivery came through the startup inbox read instead).
///
/// Stored as a string in UserDefaults; the server bounds replay to its window
/// (24 h or 1,000 events) and reports a truncated replay when the id is older.
public struct EventCursor {
    public static let key = "hca.lastEventId"
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    public var value: String? {
        get {
            guard let s = defaults.string(forKey: Self.key), !s.isEmpty else { return nil }
            return s
        }
        nonmutating set {
            if let v = newValue, !v.isEmpty { defaults.set(v, forKey: Self.key) } else { defaults.removeObject(forKey: Self.key) }
        }
    }

    /// Keep the higher of the stored id and `id` when both parse as integers
    /// (event rows are monotonic); otherwise take `id`. Returns the stored value.
    @discardableResult
    public func advance(to id: String) -> String? {
        if let cur = value, let a = Int(cur), let b = Int(id), a >= b { return cur }
        value = id
        return id
    }
}
