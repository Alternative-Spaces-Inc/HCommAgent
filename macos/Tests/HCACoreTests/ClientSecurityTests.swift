import Foundation
import XCTest
@testable import HCACore

final class ClientSecurityTests: XCTestCase {
    func testOriginValidationAndEncodedPath() throws {
        let origin = try APIOrigin("https://example.com:8443/")
        XCTAssertEqual(origin.url.absoluteString, "https://example.com:8443")
        let url = try origin.endpoint(["v1", "requests", "a/b?#%", "answer"])
        XCTAssertEqual(DeviceSigner.pathAndQuery(of: url), "/v1/requests/a%2Fb%3F%23%25/answer")
        XCTAssertEqual(url.host, "example.com")
        for invalid in ["https://u:p@example.com", "https://example.com/path", "https://example.com?x=1",
                        "https://example.com#frag", "http://example.com", "https://", " https://example.com",
                        "file:///tmp/test", "https://example.com:0", "https://example.com:65536"] {
            XCTAssertThrowsError(try APIOrigin(invalid), invalid)
        }
        XCTAssertThrowsError(try origin.endpoint(["..", "answer"]))
    }

    func testHTTPIsExplicitlyLocalDevelopmentOnly() throws {
        for host in ["localhost", "127.0.0.1", "[::1]"] {
            XCTAssertThrowsError(try APIOrigin("http://\(host):8787"))
            XCTAssertNoThrow(try APIOrigin("http://\(host):8787", allowLocalHTTP: true))
        }
        XCTAssertThrowsError(try APIOrigin("http://localhost.evil.test", allowLocalHTTP: true))
        XCTAssertThrowsError(try APIOrigin("http://192.168.1.1", allowLocalHTTP: true))
    }

    func testEveryRedirectIsRefused() {
        let original = URL(string: "https://api.example.com/v1/inbox")!
        let task = URLSession.shared.dataTask(with: original)
        for destination in ["https://api.example.com/new", "https://other.example.com", "http://api.example.com"] {
            var followed = true
            NoRedirectDelegate().urlSession(URLSession.shared, task: task,
                willPerformHTTPRedirection: HTTPURLResponse(url: original, statusCode: 302, httpVersion: nil, headerFields: nil)!,
                newRequest: URLRequest(url: URL(string: destination)!), completionHandler: { followed = $0 != nil })
            XCTAssertFalse(followed)
        }
        task.cancel()
    }

    func testDuplicateOAuthKeysAndUnauthenticatedErrorsAreRejected() {
        for query in ["state=st&state=st&code=c", "state=st&code=c&code=d", "state=st&error=e&error=e",
                      "state=st&code=c&error=e", "state=st&%73tate=st&code=c"] {
            XCTAssertEqual(GoogleOAuth.parseRedirect(requestLine: "GET /oauth2?\(query) HTTP/1.1", expectedState: "st"), .notARedirect)
        }
        XCTAssertEqual(GoogleOAuth.parseRedirect(requestLine: "GET /oauth2?error=access_denied HTTP/1.1", expectedState: "st"), .stateMismatch)
        XCTAssertEqual(GoogleOAuth.parseRedirect(requestLine: "GET /oauth2?state=evil&error=access_denied HTTP/1.1", expectedState: "st"), .stateMismatch)
        XCTAssertEqual(GoogleOAuth.parseRedirect(requestLine: "GET /oauth2?state=st&error=access_denied HTTP/1.1", expectedState: "st"), .error("access_denied"))
    }

    func testFragmentedRequestLineAndBounds() {
        var line = LoopbackRequestLine()
        for piece in ["GET /oauth2?sta", "te=st&code=a", " HTTP/1.1\r"] {
            XCTAssertEqual(line.append(Data(piece.utf8)), .pending)
        }
        XCTAssertEqual(line.append(Data("\nHost: localhost\r\n".utf8)), .line("GET /oauth2?state=st&code=a HTTP/1.1"))
        var oversized = LoopbackRequestLine()
        XCTAssertEqual(oversized.append(Data(repeating: 65, count: LoopbackRequestLine.maximumBytes + 1)), .rejected)
        var incomplete = LoopbackRequestLine()
        XCTAssertEqual(incomplete.append(Data("GET /oauth2".utf8), complete: true), .rejected)
    }

    func testRestartNotificationRecoversFromAuthorizedInboxOnly() {
        let item = InboxItem(request_id: "ask", title: "Decision", control: "radio",
                             options_or_fields: [.init(id: "a", label: "A"), .init(id: "b", label: "B")])
        let fresh = [item.request_id: item]
        XCTAssertEqual(NotificationRecovery.resolve(requestId: "ask", action: "option_1", isDefaultAction: false,
            notificationOrigin: "https://api.test", apiOrigin: "https://api.test", shownOptionIds: ["a", "b"], inbox: fresh), .answer(item, "b"))
        XCTAssertEqual(NotificationRecovery.resolve(requestId: "ask", action: "option_1", isDefaultAction: false,
            notificationOrigin: "https://api.test", apiOrigin: "https://api.test", shownOptionIds: ["a", "b"], inbox: [:]), .unavailable)
        XCTAssertEqual(NotificationRecovery.resolve(requestId: "ask", action: "option_1", isDefaultAction: false,
            notificationOrigin: "https://old.test", apiOrigin: "https://new.test", shownOptionIds: ["a", "b"], inbox: fresh), .wrongOrigin)
        XCTAssertEqual(NotificationRecovery.resolve(requestId: "ask", action: "option_1", isDefaultAction: false,
            notificationOrigin: "https://api.test", apiOrigin: "https://api.test", shownOptionIds: ["b", "a"], inbox: fresh), .changedChoices)
        XCTAssertEqual(NotificationRecovery.resolve(requestId: "ask", action: "default", isDefaultAction: true,
            notificationOrigin: "https://api.test", apiOrigin: "https://api.test", shownOptionIds: nil, inbox: fresh), .review(item))
    }

    func testResetAndRevocationControlMessagesDecodeWithoutEnvelope() {
        if case .reset = StreamMessage.decode(SSEEvent(event: "stream.reset", data: #"{"reason":"gap"}"#, id: nil)) {} else { XCTFail("reset ignored") }
        if case .revoked = StreamMessage.decode(SSEEvent(event: "stream.closed", data: #"{"reason":"authorization revoked"}"#, id: nil)) {} else { XCTFail("revocation ignored") }
        if case .ignored = StreamMessage.decode(SSEEvent(event: "stream.closed", data: #"{"reason":"superseded"}"#, id: nil)) {} else { XCTFail("supersession treated as revocation") }
    }

    func testSensitiveNotificationsRequireFullReview() {
        let item = InboxItem(request_id: "r", title: "Private question", control: "approve_deny", options_or_fields: [])
        if case .alertOnly = NotificationShaper.shape(for: item, redacted: true) {} else { XCTFail("redacted notification has answer labels") }
        XCTAssertEqual(NotificationShaper.shape(for: item, redacted: false), .approveDeny)
        let gate = InboxItem(request_id: "r", title: "Delete data", control: "approve_deny", options_or_fields: [],
                             red_gate: .init(resource: "database"))
        if case .alertOnly = NotificationShaper.shape(for: gate, redacted: false) {} else { XCTFail("red gate bypasses review") }
    }

    func testDecisionContextIncludesMaterialDetailsAndFullLabels() throws {
        let item = try JSONDecoder().decode(InboxItem.self, from: Data(#"{"request_id":"r","title":"Delete","why":"requested","control":"approve_deny","options_or_fields":[{"id":"approve","label":"Full approval description","description":"option explanation"}],"red_gate":{"resource":"database","change":"delete table","impact":"records lost","exact_command":"DROP TABLE users","rollback":"restore snapshot"},"deadline_at":"2026-10-12T20:00:00Z"}"#.utf8))
        for detail in ["Delete", "requested", "database", "delete table", "records lost", "DROP TABLE users", "restore snapshot", "Full approval description", "option explanation", "2026-10-12T20:00:00Z"] {
            XCTAssertTrue(item.decisionContext.contains(detail), detail)
        }
    }
}
