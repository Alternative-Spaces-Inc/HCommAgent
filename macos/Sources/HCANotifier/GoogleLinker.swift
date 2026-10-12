import AppKit
import Foundation
import HCACore

/// Runs the installed-app sign-in once: loopback listener, browser, code
/// exchange, then the device-signed link call. Client id comes from
/// UserDefaults key googleClientId (public value, set with defaults write).
@MainActor
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

    private let loopback = OAuthLoopbackListener()

    /// Returns the Google access token for the server to verify.
    func obtainAccessToken(loginHint: String?) async throws -> String {
        guard let clientId = UserDefaults.standard.string(forKey: Self.defaultsKey), !clientId.isEmpty else { throw LinkError.noClientId }
        let pkce = GoogleOAuth.PKCE.random()
        let state = GoogleOAuth.randomState()

        NSLog("hca.link start")
        let (port, codeStream) = try await loopback.start(expectedState: state)
        defer { Task { await loopback.stop() } }
        NSLog("hca.link listener ready on port %d", Int(port))
        let redirectURI = GoogleOAuth.redirectURI(port: port)
        let url = GoogleOAuth.authorizationURL(clientId: clientId, redirectURI: redirectURI, pkce: pkce, state: state, loginHint: loginHint)
        let opened = NSWorkspace.shared.open(url)
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
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 30
        let session = URLSession(configuration: configuration, delegate: NoRedirectDelegate(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        NSLog("hca.link exchange HTTP %d", status)
        let parsed = try? JSONDecoder().decode(GoogleOAuth.TokenResponse.self, from: data)
        guard status == 200, let token = parsed?.access_token, !token.isEmpty else {
            throw LinkError.exchangeFailed("HTTP \(status) \(parsed?.error ?? "") \(parsed?.error_description ?? "")")
        }
        return token
    }

}
