import Foundation
import XCTest
@testable import HCACore

private actor GatedRead {
    private var continuation: CheckedContinuation<Int, Never>?
    private(set) var count = 0
    func load() async -> Int {
        count += 1
        return await withCheckedContinuation { continuation = $0 }
    }
    func release(_ value: Int) { continuation?.resume(returning: value); continuation = nil }
}
final class SingleFlightTests: XCTestCase {
    private enum Failure: Error { case didNotStart }
    private static func waitUntil(_ ready: @Sendable () async -> Bool) async throws {
        for _ in 0..<200 {
            if await ready() { return }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        throw Failure.didNotStart
    }
    func testOverlappingRefreshesShareRead() async throws {
        let single = SingleFlight<Int>()
        let read = GatedRead()
        let first = Task { try await single.value { await read.load() } }
        let second = Task { try await single.value { await read.load() } }
        try await Self.waitUntil { await single.activeWaiterCount == 2 }
        try await Self.waitUntil { await read.count == 1 }
        let count = await read.count
        XCTAssertEqual(count, 1)
        await read.release(42)
        let a = try await first.value, b = try await second.value
        XCTAssertEqual(a, 42); XCTAssertEqual(b, 42)
    }

    func testCancelRejectsLateTransportResultAndAllowsNewRead() async throws {
        let single = SingleFlight<Int>()
        let read = GatedRead()
        let old = Task { try await single.value { await read.load() } }
        try await Self.waitUntil { await read.count == 1 }
        await single.cancel()
        await read.release(1) // This fake transport ignores cancellation.
        do { _ = try await old.value; XCTFail("old server result accepted") }
        catch is CancellationError { }
        let next = try await single.value { 2 }
        XCTAssertEqual(next, 2)
    }

    func testFailedReadDoesNotPoisonRetry() async throws {
        enum Failure: Error { case offline }
        let single = SingleFlight<Int>()
        do { _ = try await single.value { throw Failure.offline }; XCTFail("failure swallowed") }
        catch Failure.offline { }
        let next = try await single.value { 3 }
        XCTAssertEqual(next, 3)
    }
}
