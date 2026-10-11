import XCTest
@testable import HCACore

final class EventCursorTests: XCTestCase {
    private var suites: [String] = []

    /// A throwaway suite per test, removed in tearDown so no hca.tests.* plist
    /// is left in ~/Library/Preferences (four were, after the first run).
    private func fresh() -> UserDefaults {
        let suite = "hca.tests.\(UUID().uuidString)"
        suites.append(suite)
        return UserDefaults(suiteName: suite)!
    }

    override func tearDown() {
        for suite in suites {
            UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
            UserDefaults.standard.removeSuite(named: suite)
        }
        suites.removeAll()
        super.tearDown()
    }

    func testEmptyByDefault() {
        XCTAssertNil(EventCursor(defaults: fresh()).value)
    }

    func testPersistsAcrossInstances() {
        let d = fresh()
        EventCursor(defaults: d).advance(to: "13")
        XCTAssertEqual(EventCursor(defaults: d).value, "13")
    }

    func testAdvanceKeepsTheHigherId() {
        let d = fresh()
        let c = EventCursor(defaults: d)
        c.advance(to: "13")
        XCTAssertEqual(c.advance(to: "12"), "13")
        XCTAssertEqual(c.advance(to: "14"), "14")
        XCTAssertEqual(c.value, "14")
    }

    func testNonNumericIdReplaces() {
        let c = EventCursor(defaults: fresh())
        c.advance(to: "13")
        XCTAssertEqual(c.advance(to: "abc"), "abc")
    }

    func testClear() {
        let c = EventCursor(defaults: fresh())
        c.advance(to: "5")
        c.value = nil
        XCTAssertNil(c.value)
    }
}
