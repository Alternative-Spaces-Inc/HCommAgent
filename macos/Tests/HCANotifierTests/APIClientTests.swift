import Foundation
import XCTest
import HCACore
@testable import HCANotifier

private struct StubResponse: Sendable {
    let status: Int
    let body: String
    let cursor: String?
    init(_ body: String = "[]", status: Int = 200, cursor: String? = nil) {
        self.status = status; self.body = body; self.cursor = cursor
    }
}
private actor HTTPPlan {
    var responses: [StubResponse] = []
    var requests: [URLRequest] = []
    func reset(_ responses: [StubResponse]) { self.responses = responses; requests = [] }
    func respond(to request: URLRequest) -> StubResponse {
        requests.append(request)
        return responses.isEmpty ? StubResponse(status: 500) : responses.removeFirst()
    }
    func captured() -> [URLRequest] { requests }
}

// URLProtocol's Foundation-owned lifecycle is thread-safe. This subclass adds
// no mutable instance state; all shared test responses are confined to HTTPPlan.
private final class StubHTTP: URLProtocol, @unchecked Sendable {
    static let plan = HTTPPlan()
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "api.test" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Task {
            let reply = await Self.plan.respond(to: request)
            var headers = ["Content-Type": "application/json"]
            if let cursor = reply.cursor { headers["X-HCA-Next-Cursor"] = cursor }
            let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: headers)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(reply.body.utf8))
            client?.urlProtocolDidFinishLoading(self)
        }
    }
    override func stopLoading() {}
}

final class APIClientTests: XCTestCase {
    @MainActor private func client() throws -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubHTTP.self]
        return APIClient(signer: DeviceSigner(), origin: try APIOrigin("https://api.test"), configuration: configuration)
    }
    private static func item(_ id: String) -> String {
        #"[{"request_id":""# + id + #"","title":"Ask","control":"radio","options_or_fields":[]}]"#
    }

    @MainActor func testPaginationSignsEachCanonicalPageAndCollectsAllItems() async throws {
        await StubHTTP.plan.reset([.init(Self.item("a"), cursor: "a"), .init(Self.item("b"), cursor: "b"), .init(Self.item("c"))])
        let api = try client()
        let items = try await api.inbox()
        XCTAssertEqual(items.map(\.request_id), ["a", "b", "c"])
        let requests = await StubHTTP.plan.captured()
        XCTAssertEqual(requests.map { DeviceSigner.pathAndQuery(of: $0.url!) }, ["/v1/inbox", "/v1/inbox?after=a", "/v1/inbox?after=b"])
        for request in requests {
            let canonical = DeviceSigner.canonicalString(timestamp: request.value(forHTTPHeaderField: DeviceSigner.headerTimestamp)!,
                nonce: request.value(forHTTPHeaderField: DeviceSigner.headerNonce)!, method: "GET",
                pathAndQuery: DeviceSigner.pathAndQuery(of: request.url!), body: Data())
            XCTAssertTrue(DeviceSigner.verify(publicKeyPEM: api.signer.publicKeyPEM, canonical: canonical,
                signatureB64url: request.value(forHTTPHeaderField: DeviceSigner.headerSignature)!))
        }
        api.session.invalidateAndCancel()
    }

    @MainActor func testPaginationRejectsRepeatedCursors() async throws {
        await StubHTTP.plan.reset([.init(Self.item("a"), cursor: "repeat"), .init(Self.item("b"), cursor: "repeat")])
        let api = try client()
        do { _ = try await api.inbox(); XCTFail("repeated cursor accepted") }
        catch APIClient.ClientError.http(_, let message) { XCTAssertTrue(message.contains("cursor")) }
        api.session.invalidateAndCancel()
    }

    @MainActor func testPaginationRefusesPartialInboxAfterAuthorizationFailure() async throws {
        await StubHTTP.plan.reset([.init(Self.item("a"), cursor: "a"), .init(#"{"detail":"revoked"}"#, status: 403)])
        let api = try client()
        do { _ = try await api.inbox(); XCTFail("partial inbox returned") }
        catch APIClient.ClientError.http(let status, _) { XCTAssertEqual(status, 403) }
        api.session.invalidateAndCancel()
    }

    @MainActor func testAnswerEncodesResourceAndPreservesRetryIdentity() async throws {
        await StubHTTP.plan.reset([.init("{}", status: 201), .init("{}", status: 200)])
        let api = try client()
        let body = AnswerSubmission(control: "radio", value: "a", path_taken: ["macos_notification", "menu", "a"])
        _ = try await api.answer(requestId: "id/with?reserved#characters", body: body, idempotencyKey: "stable-retry")
        _ = try await api.answer(requestId: "id/with?reserved#characters", body: body, idempotencyKey: "stable-retry")
        let requests = await StubHTTP.plan.captured()
        XCTAssertEqual(requests.count, 2)
        XCTAssertEqual(DeviceSigner.pathAndQuery(of: requests[0].url!), "/v1/requests/id%2Fwith%3Freserved%23characters/answer")
        XCTAssertEqual(requests.map { $0.value(forHTTPHeaderField: "Idempotency-Key") }, ["stable-retry", "stable-retry"])
        XCTAssertEqual(requests[0].httpBody, requests[1].httpBody)
        XCTAssertNotEqual(requests[0].value(forHTTPHeaderField: DeviceSigner.headerNonce), requests[1].value(forHTTPHeaderField: DeviceSigner.headerNonce))
        api.session.invalidateAndCancel()
    }
}
