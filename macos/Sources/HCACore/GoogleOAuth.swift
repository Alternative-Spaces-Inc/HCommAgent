import CryptoKit
import Foundation

/// Pure pieces of the installed-app OAuth flow (PKCE, loopback redirect).
/// The app opens the browser, listens on 127.0.0.1, exchanges the code and
/// posts the access token to POST /v1/devices/self/google-link. The server
/// verifies the token against its configured client ids and owner list; the
/// app never sees a Google identity, only the server's device view.
public enum GoogleOAuth {
    public static let authEndpoint = URL(string: "https://accounts.google.com/o/oauth2/v2/auth")!
    public static let tokenEndpoint = URL(string: "https://oauth2.googleapis.com/token")!
    /// Enough for tokeninfo to report email and sub; nothing else is read.
    public static let scopes = ["openid", "email"]

    public struct PKCE: Equatable {
        public let verifier: String
        public let challenge: String

        public init(verifier: String) {
            self.verifier = verifier
            self.challenge = DeviceSigner.b64url(Data(SHA256.hash(data: Data(verifier.utf8))))
        }

        public static func random() -> PKCE {
            var bytes = [UInt8](repeating: 0, count: 32)
            for i in bytes.indices { bytes[i] = UInt8.random(in: 0...255) }
            return PKCE(verifier: DeviceSigner.b64url(Data(bytes)))
        }
    }

    public static func randomState() -> String {
        var bytes = [UInt8](repeating: 0, count: 16)
        for i in bytes.indices { bytes[i] = UInt8.random(in: 0...255) }
        return DeviceSigner.b64url(Data(bytes))
    }

    public static func redirectURI(port: UInt16) -> String { "http://127.0.0.1:\(port)/oauth2" }

    public static func authorizationURL(clientId: String, redirectURI: String, pkce: PKCE, state: String, loginHint: String? = nil) -> URL {
        var c = URLComponents(url: authEndpoint, resolvingAgainstBaseURL: false)!
        var items = [
            URLQueryItem(name: "client_id", value: clientId),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "scope", value: scopes.joined(separator: " ")),
            URLQueryItem(name: "code_challenge", value: pkce.challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "access_type", value: "online"),
            URLQueryItem(name: "prompt", value: "select_account"),
        ]
        if let hint = loginHint { items.append(URLQueryItem(name: "login_hint", value: hint)) }
        c.queryItems = items
        return c.url!
    }

    public enum RedirectResult: Equatable {
        case code(String)
        case error(String)
        case stateMismatch
        case notARedirect
    }

    /// Parses the first line of the browser's request to the loopback listener.
    public static func parseRedirect(requestLine: String, expectedState: String) -> RedirectResult {
        // "GET /oauth2?state=...&code=... HTTP/1.1"
        let parts = requestLine.split(separator: " ")
        guard parts.count >= 2, parts[0] == "GET" else { return .notARedirect }
        guard let c = URLComponents(string: "http://127.0.0.1" + parts[1]), c.path == "/oauth2" else { return .notARedirect }
        let q = Dictionary(uniqueKeysWithValues: (c.queryItems ?? []).map { ($0.name, $0.value ?? "") })
        if let err = q["error"], !err.isEmpty { return .error(err) }
        guard q["state"] == expectedState else { return .stateMismatch }
        guard let code = q["code"], !code.isEmpty else { return .notARedirect }
        return .code(code)
    }

    /// Form body for the code exchange. Google's docs mark client_secret
    /// optional for installed apps, but its token endpoint refuses a Desktop
    /// client without it ("invalid_request client_secret is missing", seen
    /// 2026-10-04). Google states the installed-app secret is not treated as
    /// confidential; the app passes it when configured and never logs it.
    public static func tokenRequestBody(clientId: String, clientSecret: String? = nil, code: String, redirectURI: String, pkce: PKCE) -> Data {
        var pairs = [
            ("client_id", clientId), ("code", code), ("code_verifier", pkce.verifier),
            ("redirect_uri", redirectURI), ("grant_type", "authorization_code"),
        ]
        if let secret = clientSecret, !secret.isEmpty { pairs.insert(("client_secret", secret), at: 1) }
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        return Data(pairs.map { "\($0)=\($1.addingPercentEncoding(withAllowedCharacters: allowed) ?? $1)" }
            .joined(separator: "&").utf8)
    }

    public struct TokenResponse: Decodable, Equatable {
        public let access_token: String?
        public let expires_in: Int?
        public let error: String?
        public let error_description: String?
    }

    /// Minimal HTML the listener answers with so the tab can be closed.
    public static func responseHTML(ok: Bool) -> String {
        let text = ok ? "Google account linked to HCA Notifier. You can close this tab." : "Sign-in did not complete. Return to HCA Notifier and try again."
        return "<!doctype html><html><head><meta charset=\"utf-8\"><title>HCA Notifier</title></head><body style=\"font-family:-apple-system,sans-serif;padding:3em\"><h2>\(text)</h2></body></html>"
    }
}
