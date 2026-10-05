import XCTest
@testable import HCACore

final class MenuAskListTests: XCTestCase {
    func item(_ id: String, title: String = "Title", control: String = "approve_deny", red: Bool = false,
              suggested: String? = nil, why: String? = nil) -> InboxItem {
        InboxItem(request_id: id, caller: "agent", title: title, why: why, control: control,
                  options_or_fields: control == "approve_deny"
                    ? [.init(id: "approve", label: "Approve"), .init(id: "deny", label: "Deny")]
                    : [.init(id: "a", label: "Alpha"), .init(id: "b", label: "Beta")],
                  suggested: suggested.map { InboxItem.Suggested(option_id: $0, why: nil) },
                  red_gate: red ? InboxItem.RedGate(resource: "r", change: "c", impact: "i") : nil)
    }

    func testRedGatesFirstThenById() {
        let rows = MenuAskList.rows(from: ["b": item("b"), "a": item("a"), "z": item("z", red: true)])
        XCTAssertEqual(rows.map(\.requestId), ["z", "a", "b"])
        XCTAssertTrue(rows[0].title.hasPrefix("RED GATE: "))
        XCTAssertFalse(rows[1].title.hasPrefix("RED GATE"))
    }

    func testTruncation() {
        let long = String(repeating: "x", count: 120)
        let row = MenuAskList.row(for: item("1", title: long))
        XCTAssertEqual(row.title.count, MenuAskList.titleLimit)
        XCTAssertTrue(row.title.hasSuffix("…"))
        XCTAssertEqual(MenuAskList.truncate("line one\nline two"), "line one line two")
    }

    func testSuggestedMarker() {
        let row = MenuAskList.row(for: item("1", control: "radio", suggested: "b", why: "because"))
        XCTAssertEqual(row.choices.map(\.optionId), ["a", "b"])
        XCTAssertEqual(row.choices[1].title, "Beta (suggested)")
        XCTAssertTrue(row.choices[1].suggested)
        XCTAssertFalse(row.choices[0].suggested)
        XCTAssertEqual(row.why, "because")
        XCTAssertTrue(row.answerable)
    }

    func testFreeTextNotAnswerableFromMenu() {
        let it = InboxItem(request_id: "t", title: "Type something", control: "text", options_or_fields: [])
        let row = MenuAskList.row(for: it)
        XCTAssertFalse(row.answerable)
        XCTAssertEqual(MenuAskList.rows(from: [:]), [])
    }
}
