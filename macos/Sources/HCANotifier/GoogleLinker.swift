import AppKit
import Foundation
import HCACore
import Network

/// Runs the installed-app sign-in once: loopback listener, browser, code
/// exchange, then the device-signed link call. Client id comes from
/// UserDefaults key googleClientId (public value, set with defaults write).
final class GoogleLinker {
    static let defaultsKey = "googleClientId"
    static let secretDefaultsKey = "googleClientSecret"

    enum LinkError: Error, LocalizedError {
        case noClientId, listenFailed, browserFailed, denied(String), stateMismatch, exchangeFailed(String), timeout
        var errorDescription: String? {
            switch self {
            case .noClientId: return "no Google client id set (defaults write com.alternativespaces.hca.notifier googleClientId ...)"
            case .listenFailed: return "could not open the loopback listener"
            case .browserFailed: return "could not open the browser"
            case .denied(let e): return "Google returned \(e)"
            case .stateMismatch: return "redirect state did not match"
            case .exchangeFailed(let e): return "code exchange failed: \(e)"
            case .timeout: return "no sign-in within 5 minutes"
            }
        }
    }

    private var listener: NWListener?
    private var connections: [NWConnection] = []

    /// Returns the Google access token for the server to verify.
    func obtainAccessToken(loginHint: String?) async throws -> String {
        guard let clientId = UserDefaults.standard.string(forKey: Self.defaultsKey), !clientId.isEmpty else { throw LinkError.noClientId }
        let pkce = GoogleOAuth.PKCE.random()
        let state = GoogleOAuth.randomState()

        NSLog("hca.link start")
        let (port, codeStream) = try await startListener(expectedState: state)
        defer { stopListener() }
        NSLog("hca.link listener ready on port %d", Int(port))
        let redirectURI = GoogleOAuth.redirectURI(port: port)
        let url = GoogleOAuth.authorizationURL(clientId: clientId, redirectURI: redirectURI, pkce: pkce, state: state, loginHint: loginHint)
        let opened = await MainActor.run { NSWorkspace.shared.open(url) }
        NSLog("hca.link browser open: %@", opened ? "yes" : "no")
        guard opened else { throw LinkError.browserFailed }

        let code = try await withThrowingTaskGroup(of: String.self) { group -> String in
            group.addTask {
                for try await c in codeStream { return c }
                throw LinkError.timeout
            }
            group.addTask {
                try await Task.sleep(nanoseconds: 300 * 1_000_000_000)
                throw LinkError.timeout
            }
            let first = try await group.next()!
            group.cancelAll()
            return first
        }

        var request = URLRequest(url: GoogleOAuth.tokenEndpoint)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        let secret = UserDefaults.standard.string(forKey: Self.secretDefaultsKey)
        request.httpBody = GoogleOAuth.tokenRequestBody(clientId: clientId, clientSecret: secret, code: code, redirectURI: redirectURI, pkce: pkce)
        NSLog("hca.link code received, exchanging")
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        NSLog("hca.link exchange HTTP %d", status)
        let parsed = try? JSONDecoder().decode(GoogleOAuth.TokenResponse.self, from: data)
        guard status == 200, let token = parsed?.access_token, !token.isEmpty else {
            throw LinkError.exchangeFailed("HTTP \(status) \(parsed?.error ?? "") \(parsed?.error_description ?? "")")
        }
        return token
    }

    private let queue = DispatchQueue(label: "hca.oauth.loopback")

    /// Starts the loopback listener and resolves once it is bound to a port.
    private func startListener(expectedState: String) async throws -> (UInt16, AsyncThrowingStream<String, Error>) {
        let params = NWParameters.tcp
        params.requiredInterfaceType = .loopback
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: .any)
        let listener: NWListener
        do { listener = try NWListener(using: params) } catch {
            NSLog("hca.link listener init failed: %@", String(describing: error))
            throw LinkError.listenFailed
        }
        self.listener = listener

        var yield: ((String) -> Void)?
        var finish: ((Error?) -> Void)?
        let stream = AsyncThrowingStream<String, Error> { continuation in
            yield = { continuation.yield($0) }
            finish = { err in if let e = err { continuation.finish(throwing: e) } else { continuation.finish() } }
        }

        listener.newConnectionHandler = { [weak self] conn in
            self?.connections.append(conn)
            conn.start(queue: self?.queue ?? .global())
            conn.receive(minimumIncompleteLength: 1, maximumLength: 16384) { data, _, _, _ in
                let text = data.map { String(decoding: $0, as: UTF8.self) } ?? ""
                let firstLine = text.split(separator: "\r\n", maxSplits: 1).first.map(String.init) ?? ""
                let result = GoogleOAuth.parseRedirect(requestLine: firstLine, expectedState: expectedState)
                var ok = false
                if case .code = result { ok = true }
                let html = GoogleOAuth.responseHTML(ok: ok)
                let head = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: \(html.utf8.count)\r\nConnection: close\r\n\r\n"
                conn.send(content: Data((head + html).utf8), completion: .contentProcessed { _ in conn.cancel() })
                switch result {
                case .code(let c): yield?(c); finish?(nil)
                case .error(let e): finish?(LinkError.denied(e))
                case .stateMismatch: finish?(LinkError.stateMismatch)
                case .notARedirect: break
                }
            }
        }

        final class Once { var done = false }
        let once = Once()
        let port: UInt16 = try await withCheckedThrowingContinuation { cont in
            listener.stateUpdateHandler = { state in
                NSLog("hca.link listener state: %@", String(describing: state))
                guard !once.done else { return }
                switch state {
                case .ready:
                    once.done = true
                    cont.resume(returning: listener.port?.rawValue ?? 0)
                case .failed, .cancelled, .waiting:
                    once.done = true
                    cont.resume(throwing: LinkError.listenFailed)
                default: break
                }
            }
            listener.start(queue: self.queue)
        }
        guard port != 0 else { stopListener(); throw LinkError.listenFailed }
        return (port, stream)
    }

    private func stopListener() {
        listener?.cancel(); listener = nil
        connections.forEach { $0.cancel() }; connections.removeAll()
    }
}
