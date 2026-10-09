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
        let program = "/Users/x/hca-notifier/HCA Notifier.app/Contents/MacOS/HCANotifier"
        let data = Data(LaunchAgentPlist.contents(program: program).utf8)
        let obj = try PropertyListSerialization.propertyList(from: data, format: nil)
        let dict = try XCTUnwrap(obj as? [String: Any])
        XCTAssertEqual(dict["Label"] as? String, "com.alternativespaces.hca.notifier")
        XCTAssertEqual(dict["Program"] as? String, program)
        XCTAssertEqual(dict["RunAtLoad"] as? Bool, true)
        let keepAlive = try XCTUnwrap(dict["KeepAlive"] as? [String: Any])
        XCTAssertEqual(keepAlive["SuccessfulExit"] as? Bool, false)
        XCTAssertEqual(dict["ProcessType"] as? String, "Interactive")
        let env = try XCTUnwrap(dict["EnvironmentVariables"] as? [String: String])
        XCTAssertEqual(env["HCA_LAUNCHD_AGENT"], "1")
        XCTAssertNil(dict["BundleProgram"])
    }

    func testLaunchAgentPlistEscapesAndPath() throws {
        let data = Data(LaunchAgentPlist.contents(program: "/a/b&c/<x>/HCANotifier").utf8)
        let dict = try XCTUnwrap(try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
        XCTAssertEqual(dict["Program"] as? String, "/a/b&c/<x>/HCANotifier")
        XCTAssertEqual(LaunchAgentPlist.path(home: "/Users/x"),
                       "/Users/x/Library/LaunchAgents/com.alternativespaces.hca.notifier.plist")
    }

    func testRunningPidFromLaunchctlPrint() {
        XCTAssertNil(LaunchAgentPlist.runningPid(launchctlPrint: nil))
        XCTAssertNil(LaunchAgentPlist.runningPid(launchctlPrint: "x = {\n\tstate = not running\n}"))
        XCTAssertEqual(LaunchAgentPlist.runningPid(launchctlPrint: "x = {\n\tstate = running\n\tpid = 4242\n}"), 4242)
    }

    func testDuplicateInstanceRule() {
        XCTAssertEqual(LaunchAgentPlist.duplicateAction(otherRunning: false, selfIsLaunchd: false), .none)
        XCTAssertEqual(LaunchAgentPlist.duplicateAction(otherRunning: false, selfIsLaunchd: true), .none)
        XCTAssertEqual(LaunchAgentPlist.duplicateAction(otherRunning: true, selfIsLaunchd: false), .exitSelf)
        XCTAssertEqual(LaunchAgentPlist.duplicateAction(otherRunning: true, selfIsLaunchd: true), .terminateOther)
    }

}
