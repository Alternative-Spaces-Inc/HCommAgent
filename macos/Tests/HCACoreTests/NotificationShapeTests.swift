import XCTest
@testable import HCACore

final class NotificationShapeTests: XCTestCase {
    func ask(_ control: String, _ options: [(String, String)] = [], suggested: String? = nil) -> AskSummary {
        AskSummary(requestId: "r1", control: control, title: "T",
                   options: options.map { (value: $0.0, label: $0.1) }, suggestedValue: suggested)
    }

    func testApproveDeny() {
        XCTAssertEqual(NotificationShaper.shape(for: ask("approve_deny")), .approveDeny)
    }

    func testRadioUpToFourGetsOneActionPerOption() {
        let a = ask("radio", [("a", "Alpha"), ("b", "Beta")])
        guard case .radio(let actions) = NotificationShaper.shape(for: a) else { return XCTFail("not radio") }
        XCTAssertEqual(actions.map { $0.identifier }, ["option_0", "option_1"])
        XCTAssertEqual(actions.map { $0.value }, ["a", "b"])
        XCTAssertEqual(actions.map { $0.title }, ["Alpha", "Beta"])
    }

    func testRadioFiveOptionsFallsBackToAlert() {
        let a = ask("radio", (0..<5).map { ("v\($0)", "L\($0)") })
        guard case .alertOnly = NotificationShaper.shape(for: a) else { return XCTFail("expected alertOnly") }
    }

    func testRadioNoOptionsFallsBackToAlert() {
        guard case .alertOnly = NotificationShaper.shape(for: ask("radio")) else { return XCTFail("expected alertOnly") }
    }

    func testOtherControlsFallBackToAlert() {
        for control in ["text", "checkbox", "number", "date"] {
            guard case .alertOnly = NotificationShaper.shape(for: ask(control)) else { return XCTFail(control) }
        }
    }

    func testBodyNamesSuggestedAndFallbackHint() {
        let a = ask("text", [("x", "Ex")], suggested: "x")
        let shape = NotificationShaper.shape(for: a)
        let body = NotificationShaper.body(for: a, shape: shape)
        XCTAssertTrue(body.contains("Suggested: Ex"))
        XCTAssertTrue(body.contains("Answer in Chrome"))
    }

    func testIdentifierRoundTrip() {
        let id = NotificationShaper.notificationIdentifier(for: "abc123")
        XCTAssertEqual(id, "hca-abc123")
        XCTAssertEqual(NotificationShaper.requestId(fromNotificationIdentifier: id), "abc123")
        XCTAssertNil(NotificationShaper.requestId(fromNotificationIdentifier: "other-1"))
    }
}
