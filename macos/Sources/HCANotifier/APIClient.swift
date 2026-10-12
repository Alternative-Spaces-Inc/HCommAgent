import Foundation
import HCACore

/// Signed calls to the HCA API. Base URL comes from UserDefaults key
/// apiBaseURL (set once: defaults write com.alternativespaces.hca.notifier apiBaseURL URL).
@MainActor
final class APIClient {
    enum ClientError: Error, LocalizedError {
        case noBaseURL
        case http(Int, String)
        var errorDescription: String? {
            switch self {
            case .noBaseURL: return "no API URL set (defaults write com.alternativespaces.hca.notifier apiBaseURL ...)"
            case .http(let code, let body): return "HTTP \(code) \(body.prefix(160))"
            }
        }
    }

    static let defaultsKey = "apiBaseURL"
    let signer: DeviceSigner
    let session: URLSession
    private let origin: APIOrigin?

    init(signer: DeviceSigner, origin: APIOrigin? = nil, configuration: URLSessionConfiguration = .ephemeral) {
        self.signer = signer
        self.origin = origin ?? UserDefaults.standard.string(forKey: Self.defaultsKey).flatMap {
            try? APIOrigin($0, allowLocalHTTP: UserDefaults.standard.bool(forKey: "allowLocalHTTPDevelopment"))
        }
        let cfg = configuration
        cfg.timeoutIntervalForRequest = 30
        cfg.waitsForConnectivity = false
        self.session = URLSession(configuration: cfg, delegate: NoRedirectDelegate(), delegateQueue: nil)
    }

    var baseURL: URL? { origin?.url }

    private func url(_ components: [String]) throws -> URL {
        guard let origin else { throw ClientError.noBaseURL }
        return try origin.endpoint(components)
    }

    private func send(_ method: String, _ path: [String], body: Data? = nil, idempotencyKey: String? = nil) async throws -> (Int, Data) {
        var headers = ["Accept": "application/json"]
        if let idempotencyKey { headers["Idempotency-Key"] = idempotencyKey }
        let request = try signer.signedRequest(method: method, url: try url(path), body: body, extraHeaders: headers)
        let (data, response) = try await session.data(for: request)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        return (code, data)
    }

    struct DeviceSelf: Decodable {
        let device_id: String
        let label: String?
        let surface: String?
        let google_email: String?
        let priority: Int?
    }

    func deviceSelf() async throws -> DeviceSelf {
        let (code, data) = try await send("GET", ["v1", "devices", "self"])
        guard code == 200 else { throw ClientError.http(code, String(decoding: data, as: UTF8.self)) }
        return try JSONDecoder().decode(DeviceSelf.self, from: data)
    }

    /// PATCH /v1/devices/self with the ladder place the owner picked in the menu (HCA-P14 priority controls).
    func setPriority(_ priority: Int) async throws -> DeviceSelf {
        let body = try JSONSerialization.data(withJSONObject: ["priority": priority])
        let (code, data) = try await send("PATCH", ["v1", "devices", "self"], body: body)
        guard code == 200 else { throw ClientError.http(code, String(decoding: data, as: UTF8.self)) }
        return try JSONDecoder().decode(DeviceSelf.self, from: data)
    }

    /// POST /v1/devices/self/google-link with the access token the browser flow produced.
    func googleLink(accessToken: String) async throws -> DeviceSelf {
        let body = try JSONSerialization.data(withJSONObject: ["access_token": accessToken])
        let (code, data) = try await send("POST", ["v1", "devices", "self", "google-link"], body: body)
        guard code == 200 else { throw ClientError.http(code, String(decoding: data, as: UTF8.self)) }
        return try JSONDecoder().decode(DeviceSelf.self, from: data)
    }

    func inbox() async throws -> [InboxItem] {
        var items: [InboxItem] = []
        var after: String?
        var seenCursors: Set<String> = []
        var seenItems: Set<String> = []
        for _ in 0..<50 {
            try Task.checkCancellation()
            var endpoint = URLComponents(url: try url(["v1", "inbox"]), resolvingAgainstBaseURL: false)!
            if let after { endpoint.queryItems = [URLQueryItem(name: "after", value: after)] }
            let request = try signer.signedRequest(method: "GET", url: endpoint.url!, extraHeaders: ["Accept": "application/json"])
            let (data, response) = try await session.data(for: request)
            let http = response as? HTTPURLResponse
            let code = http?.statusCode ?? 0
            guard code == 200 else { throw ClientError.http(code, String(decoding: data, as: UTF8.self)) }
            let page = try JSONDecoder().decode([InboxItem].self, from: data)
            for item in page {
                guard seenItems.insert(item.request_id).inserted else { throw ClientError.http(0, "duplicate inbox item during pagination; retry refresh") }
                items.append(item)
            }
            guard let next = http?.value(forHTTPHeaderField: "X-HCA-Next-Cursor"), !next.isEmpty else { return items }
            guard next.utf8.count <= 512, seenCursors.insert(next).inserted else { throw ClientError.http(0, "invalid or repeated inbox cursor") }
            after = next
        }
        throw ClientError.http(0, "inbox pagination exceeded 50 pages")
    }

    enum AnswerOutcome { case recorded, alreadyAnswered, gone, refused(Int, String) }

    func answer(requestId: String, body: AnswerSubmission, idempotencyKey: String) async throws -> AnswerOutcome {
        let (code, data) = try await send("POST", ["v1", "requests", requestId, "answer"], body: try body.encoded(), idempotencyKey: idempotencyKey)
        switch code {
        case 200, 201: return .recorded
        case 409: return .alreadyAnswered
        case 404, 410: return .gone
        default: return .refused(code, String(decoding: data, as: UTF8.self))
        }
    }

    /// Opens the SSE stream and yields parsed envelopes until the server
    /// closes it or the task is cancelled. Throws on a non-200 open.
    /// HCA-P20: `lastEventId` is the `id:` line of the last envelope this
    /// client saw; sent as Last-Event-ID so the server replays what was
    /// missed while reconnecting (24 h or 1,000 events), then goes live.
    /// `onEvent` receives the envelope and its id line (nil on old servers).
    func stream(lastEventId: String? = nil, onOpen: @escaping @MainActor () -> Void,
                onEvent: @escaping @MainActor (StreamEnvelope, String?) -> Void,
                onReset: @escaping @MainActor () -> Void) async throws {
        var extra = ["Accept": "text/event-stream"]
        if let last = lastEventId, !last.isEmpty { extra["Last-Event-ID"] = last }
        let request = try signer.signedRequest(method: "GET", url: try url(["v1", "inbox", "stream"]), body: nil,
                                               extraHeaders: extra)
        var cfg = request
        cfg.timeoutInterval = 3600
        let (bytes, response) = try await session.bytes(for: cfg)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else {
            var body = Data()
            for try await b in bytes.prefix(2000) { body.append(b) }
            throw ClientError.http(code, String(decoding: body, as: UTF8.self))
        }
        onOpen()
        // Read raw bytes, not `bytes.lines`: AsyncLineSequence drops empty
        // lines (probed on macOS 27 on 2026-10-11: "a\n\nb\n" yields a, b), and
        // the empty line is the SSE frame boundary. With `.lines` the parser
        // never dispatched an event, so every ask reached this app through the
        // 60 s inbox poll and Last-Event-ID never advanced (P20 live proof:
        // replays 0, 306 s to the banner).
        let parser = SSEParser()
        var chunk = Data()
        for try await byte in bytes {
            try Task.checkCancellation()
            guard chunk.count < 256 * 1024 else { throw ClientError.http(0, "stream line exceeded 256 KiB") }
            chunk.append(byte)
            if byte == 0x0A {
                for ev in parser.feed(chunk) {
                    switch StreamMessage.decode(ev) {
                    case .reset: onReset()
                    case .revoked: throw ClientError.http(403, "authorization revoked")
                    case .envelope(let env, let id): onEvent(env, id)
                    case .ignored: break
                    }
                }
                chunk.removeAll(keepingCapacity: true)
            }
        }
    }
}
