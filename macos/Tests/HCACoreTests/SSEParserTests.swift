import XCTest
@testable import HCACore

/// HCA-P20 Day 5 follow-up: the live frame as the server writes it (events.py
/// format_sse), fed the way APIClient.stream feeds it: one raw chunk per line,
/// newline included, empty lines included. AsyncLineSequence drops the empty
/// lines, which is why the app reads bytes itself; this test pins the contract
/// the byte reader relies on.
final class SSEParserTests: XCTestCase {
    private let frame = ": connected\n\nid: 2\nevent: request.created\ndata: {\"id\": 2, \"event\": \"request.created\", \"data\": {\"request_id\": \"r1\", \"visible_to\": [\"dev_a\"]}, \"timestamp\": \"2026-10-11T20:44:19+00:00\"}\n\nid: 3\nevent: request.cancelled\ndata: {\"id\": 3, \"event\": \"request.cancelled\", \"data\": {\"request_id\": \"r1\"}}\n\n"

    private func feedLineByLine(_ text: String) -> [SSEEvent] {
        let parser = SSEParser()
        var out: [SSEEvent] = []
        var chunk = Data()
        for byte in Array(text.utf8) {
            chunk.append(byte)
            if byte == 0x0A { out += parser.feed(chunk); chunk.removeAll() }
        }
        return out
    }

    func testLiveFrameYieldsTwoEventsWithIds() {
        let events = feedLineByLine(frame)
        XCTAssertEqual(events.map { $0.event }, ["request.created", "request.cancelled"])
        XCTAssertEqual(events.map { $0.id }, ["2", "3"])
        let env = try? JSONDecoder().decode(StreamEnvelope.self, from: Data(events[0].data.utf8))
        XCTAssertEqual(env?.data.request_id, "r1")
        XCTAssertEqual(env?.data.visible_to, ["dev_a"])
    }

    func testCommentOnlyYieldsNothing() {
        XCTAssertTrue(feedLineByLine(": connected\n\n: ping\n\n").isEmpty)
    }

    func testWithoutEmptyLinesNothingDispatches() {
        // What `.lines` handed the parser before the fix: no frame boundary, no event.
        let stripped = frame.split(separator: "\n").joined(separator: "\n") + "\n"
        XCTAssertTrue(feedLineByLine(stripped).isEmpty)
    }

    func testChunkSplitInsideALine() {
        let parser = SSEParser()
        var out = parser.feed(Data("id: 7\nevent: request.exp".utf8))
        out += parser.feed(Data("ired\ndata: {\"a\":1}\n\n".utf8))
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].event, "request.expired")
        XCTAssertEqual(out[0].id, "7")
    }
}
