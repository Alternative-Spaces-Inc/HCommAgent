import Foundation

/// Concurrent refresh requests share one in-flight read. Cancellation invalidates
/// even a transport that returns an old result after being cancelled.
public actor SingleFlight<Value: Sendable> {
    private var active: (id: UUID, task: Task<Value, Error>)?
    private var generation = 0
    private(set) var activeWaiterCount = 0
    public init() {}

    public func value(load: @escaping @Sendable () async throws -> Value) async throws -> Value {
        activeWaiterCount += 1
        defer { activeWaiterCount -= 1 }
        let epoch = generation
        let current: (id: UUID, task: Task<Value, Error>)
        if let active { current = active }
        else { current = (UUID(), Task { try await load() }); active = current }
        defer { if active?.id == current.id { active = nil } }
        let value = try await current.task.value
        try Task.checkCancellation()
        guard epoch == generation else { throw CancellationError() }
        return value
    }

    public func cancel() {
        generation += 1
        active?.task.cancel()
        active = nil
    }
}
