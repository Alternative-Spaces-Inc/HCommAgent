import XCTest
@testable import HCACore

final class LivenessTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    func testNoReadYetIsWaitingNotStale() {
        let l = Liveness()
        XCTAssertFalse(l.isStale(now: t0))
        XCTAssertEqual(l.title(now: t0), "Last inbox read: waiting for the first read, stream down, polling")
    }

    func testFreshReadWithStream() {
        var l = Liveness(streamConnected: true)
        l.recordRead(at: t0)
        XCTAssertFalse(l.isStale(now: t0))
        XCTAssertEqual(l.title(now: t0.addingTimeInterval(12)), "Last inbox read: 12 s ago, stream connected")
    }

    func testStaleBoundary() {
        var l = Liveness(streamConnected: false)
        l.recordRead(at: t0)
        XCTAssertFalse(l.isStale(now: t0.addingTimeInterval(179)))
        XCTAssertTrue(l.isStale(now: t0.addingTimeInterval(180)))
        XCTAssertEqual(l.title(now: t0.addingTimeInterval(179)), "Last inbox read: 2 min ago, stream down, polling")
        XCTAssertEqual(l.title(now: t0.addingTimeInterval(360)), "Last inbox read: 6 min ago, stale")
    }

    func testAgeText() {
        XCTAssertEqual(Liveness.ageText(0), "0 s ago")
        XCTAssertEqual(Liveness.ageText(59.9), "59 s ago")
        XCTAssertEqual(Liveness.ageText(60), "1 min ago")
        XCTAssertEqual(Liveness.ageText(7200), "2 h ago")
        XCTAssertEqual(Liveness.ageText(-5), "0 s ago")
    }

    func testLaunchAgentPlistParses() throws {
        let data = Data(LaunchAgentPlist.contents().utf8)
        let obj = try PropertyListSerialization.propertyList(from: data, options: [], format: nil)
        let dict = try XCTUnwrap(obj as? [String: Any])
        XCTAssertEqual(dict["Label"] as? String, "com.alternativespaces.hca.notifier")
        XCTAssertEqual(dict["BundleProgram"] as? String, "Contents/MacOS/HCANotifier")
        XCTAssertEqual(dict["RunAtLoad"] as? Bool, true)
        let keepAlive = try XCTUnwrap(dict["KeepAlive"] as? [String: Any])
        XCTAssertEqual(keepAlive["SuccessfulExit"] as? Bool, false)
        XCTAssertEqual(dict["ProcessType"] as? String, "Interactive")
    }

    /// bundle.sh copies macos/scripts/launchagent.plist into the bundle; keep
    /// the file and the Swift rendering identical so neither drifts.
    func testLaunchAgentPlistMatchesScriptFile() throws {
        let here = URL(fileURLWithPath: #filePath)
        let file = here.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("scripts").appendingPathComponent("launchagent.plist")
        let onDisk = try String(contentsOf: file, encoding: .utf8)
        XCTAssertEqual(onDisk, LaunchAgentPlist.contents())
    }
}
