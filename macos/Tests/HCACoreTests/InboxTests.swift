import Foundation
import XCTest
@testable import HCACore

final class InboxTests: XCTestCase {
    let createdJSON = """
    {"event":"request.created","data":{"request_id":"r1","caller":"agent-x","audience":"owner","title":"Merge PR 7?","why":"gates green","control":"approve_deny","options_or_fields":[{"id":"approve","label":"Approve"},{"id":"deny","label":"Deny"}],"suggested":{"option_id":"approve","why":"all green"},"deliver_to":["browser_overlay"]},"timestamp":"2026-10-04T20:00:00+00:00"}
    """
    let answeredJSON = """
    {"event":"request.answered","data":{"request_id":"r1","control":"approve_deny","value":"approve","surface":"browser_overlay"},"timestamp":"2026-10-04T20:01:00+00:00"}
    """

    func testSSEParserHandlesSplitChunksCommentsAndCRLF() {
        let p = SSEParser()
        var events = p.feed(Data(": keepalive\r\n\r\nevent: request.created\r\ndata: {\"a\":".utf8))
        XCTAssertEqual(events, [])
        events += p.feed(Data("1}\r\n\r\nevent: request.answered\ndata: line1\ndata: line2\n\n".utf8))
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events[0], SSEEvent(event: "request.created", data: "{\"a\":1}", id: nil))
        XCTAssertEqual(events[1].event, "request.answered")
        XCTAssertEqual(events[1].data, "line1\nline2")
    }

    func testStreamEnvelopeDecodesCreatedAndAnswered() throws {
        let created = try JSONDecoder().decode(StreamEnvelope.self, from: Data(createdJSON.utf8))
        XCTAssertEqual(created.event, "request.created")
        XCTAssertEqual(created.data.item?.request_id, "r1")
        XCTAssertEqual(created.data.item?.options_or_fields.map { $0.id }, ["approve", "deny"])
        XCTAssertEqual(created.data.item?.suggested?.option_id, "approve")
        let answered = try JSONDecoder().decode(StreamEnvelope.self, from: Data(answeredJSON.utf8))
        XCTAssertEqual(answered.data.request_id, "r1")
        XCTAssertNil(answered.data.item, "an answer record is not an inbox item")
    }

    func testStoreDedupesStreamAndPoll() throws {
        var store = InboxStore()
        let created = try JSONDecoder().decode(StreamEnvelope.self, from: Data(createdJSON.utf8))
        let d1 = store.apply(event: created)
        XCTAssertEqual(d1.added.map { $0.request_id }, ["r1"])
        // The poll then returns the same item: nothing new.
        let d2 = store.applyInbox([created.data.item!])
        XCTAssertEqual(d2, InboxStore.Delta())
        // Same event replayed after a reconnect: nothing new.
        XCTAssertEqual(store.apply(event: created), InboxStore.Delta())
        XCTAssertEqual(store.count, 1)
        // Answered elsewhere: removed.
        let answered = try JSONDecoder().decode(StreamEnvelope.self, from: Data(answeredJSON.utf8))
        XCTAssertEqual(store.apply(event: answered).removed, ["r1"])
        XCTAssertEqual(store.count, 0)
        // A poll that no longer lists an item removes it too.
        _ = store.apply(event: created)
        XCTAssertEqual(store.applyInbox([]).removed, ["r1"])
    }

    func testActionMapperApproveDenyAndRadio() {
        let ad = InboxItem(request_id: "r", title: "t", control: "approve_deny",
                           options_or_fields: [.init(id: "approve", label: "Approve"), .init(id: "deny", label: "Deny")])
        XCTAssertEqual(ActionMapper.optionId(for: "approve", item: ad), "approve")
        XCTAssertEqual(ActionMapper.optionId(for: "deny", item: ad), "deny")
        XCTAssertNil(ActionMapper.optionId(for: "com.apple.UNNotificationDefaultActionIdentifier", item: ad))
        let custom = InboxItem(request_id: "r", title: "t", control: "approve_deny",
                               options_or_fields: [.init(id: "yes", label: "Yes"), .init(id: "no", label: "No")])
        XCTAssertEqual(ActionMapper.optionId(for: "approve", item: custom), "yes")
        XCTAssertEqual(ActionMapper.optionId(for: "deny", item: custom), "no")
        let radio = InboxItem(request_id: "r", title: "t", control: "radio",
                              options_or_fields: [.init(id: "a", label: "A"), .init(id: "b", label: "B"), .init(id: "c", label: "C")])
        XCTAssertEqual(ActionMapper.optionId(for: "option_1", item: radio), "b")
        XCTAssertNil(ActionMapper.optionId(for: "option_7", item: radio))
        let text = InboxItem(request_id: "r", title: "t", control: "chat", options_or_fields: [])
        XCTAssertNil(ActionMapper.optionId(for: "approve", item: text))
    }

    func testAnswerSubmissionEncodesStably() throws {
        let body = AnswerSubmission(control: "approve_deny", value: "approve", path_taken: ["macos_notification", "banner", "approve"], duration_ms: 1200)
        let json = String(decoding: try body.encoded(), as: UTF8.self)
        XCTAssertEqual(json, #"{"control":"approve_deny","duration_ms":1200,"path_taken":["macos_notification","banner","approve"],"surface":"macos_notification","value":"approve"}"#)
    }

    // HCA-P14 surface routing
    let escalatedJSON = """
    {"event":"request.escalated","data":{"request_id":"r2","visible_to":["dev_mac","dev_chrome"],"caller":"agent-x","audience":"owner","title":"Late ask","control":"approve_deny","options_or_fields":[{"id":"approve","label":"Approve"},{"id":"deny","label":"Deny"}],"suggested":{"option_id":"approve","why":"w"},"deliver_to":["browser_overlay"]},"timestamp":"2026-10-05T16:00:00+00:00"}
    """

    func testVisibleToDecodesAndAddressing() throws {
        let e = try JSONDecoder().decode(StreamEnvelope.self, from: Data(escalatedJSON.utf8))
        XCTAssertEqual(e.event, "request.escalated")
        XCTAssertEqual(e.data.visible_to, ["dev_mac", "dev_chrome"])
        XCTAssertEqual(e.data.item?.request_id, "r2")
        XCTAssertTrue(InboxStore.isAddressed(e, to: "dev_mac"))
        XCTAssertFalse(InboxStore.isAddressed(e, to: "dev_other"))
        XCTAssertFalse(InboxStore.isAddressed(e, to: nil))
        let old = try JSONDecoder().decode(StreamEnvelope.self, from: Data(createdJSON.utf8))
        XCTAssertNil(old.data.visible_to)
        XCTAssertTrue(InboxStore.isAddressed(old, to: "anyone"))
        XCTAssertTrue(InboxStore.isAddressed(old, to: nil))
    }

    func testStoreIgnoresEnvelopesNotAddressedToThisDevice() throws {
        var store = InboxStore()
        let e = try JSONDecoder().decode(StreamEnvelope.self, from: Data(escalatedJSON.utf8))
        XCTAssertEqual(store.apply(event: e, deviceId: "dev_other").added.count, 0)
        XCTAssertEqual(store.count, 0)
        XCTAssertEqual(store.apply(event: e, deviceId: "dev_mac").added.map { $0.request_id }, ["r2"])
        XCTAssertEqual(store.count, 1)
        let old = try JSONDecoder().decode(StreamEnvelope.self, from: Data(createdJSON.utf8))
        XCTAssertEqual(store.apply(event: old, deviceId: "dev_other").added.map { $0.request_id }, ["r1"])
    }

    func testRepostDueOnlyForClearedAndAgedItems() throws {
        var store = InboxStore()
        let e = try JSONDecoder().decode(StreamEnvelope.self, from: Data(escalatedJSON.utf8))
        _ = store.apply(event: e, deviceId: "dev_mac")
        let old = try JSONDecoder().decode(StreamEnvelope.self, from: Data(createdJSON.utf8))
        _ = store.apply(event: old)
        let now = Date()
        let shownAt = ["r1": now.addingTimeInterval(-200), "r2": now.addingTimeInterval(-10)]
        XCTAssertEqual(store.repostDue(delivered: ["r1"], shownAt: shownAt, now: now, window: 90).map { $0.request_id }, [])
        XCTAssertEqual(store.repostDue(delivered: [], shownAt: shownAt, now: now, window: 90).map { $0.request_id }, ["r1"])
        XCTAssertEqual(store.repostDue(delivered: [], shownAt: [:], now: now, window: 90).map { $0.request_id }, ["r1", "r2"])
    }

    // HCA-P14 priority controls
    func testSurfacePriorityPlacesAndLabels() {
        XCTAssertEqual(SurfacePriority.allCases.map { $0.rawValue }, [10, 100, 200])
        XCTAssertEqual(SurfacePriority.label(for: 10), "Priority: Primary (10)")
        XCTAssertEqual(SurfacePriority.label(for: 200), "Priority: Fallback (200)")
        XCTAssertEqual(SurfacePriority.label(for: 100), "Priority: Default (100)")
        XCTAssertEqual(SurfacePriority.label(for: 37), "Priority: custom (37)")
        XCTAssertEqual(SurfacePriority.label(for: nil), "Priority: unknown")
    }
}
