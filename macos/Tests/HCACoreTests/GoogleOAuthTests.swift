import Foundation
import XCTest
@testable import HCACore

final class GoogleOAuthTests: XCTestCase {
    func testPKCEChallengeMatchesRFC7636Appendix() {
        // RFC 7636 appendix B example.
        let pkce = GoogleOAuth.PKCE(verifier: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        XCTAssertEqual(pkce.challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        let r = GoogleOAuth.PKCE.random()
        XCTAssertEqual(r.verifier.count, 43)
        XCTAssertFalse(r.verifier.contains("="))
    }

    func testAuthorizationURLCarriesEveryParameter() throws {
        let pkce = GoogleOAuth.PKCE(verifier: "v")
        let url = GoogleOAuth.authorizationURL(clientId: "cid.apps.googleusercontent.com", redirectURI: GoogleOAuth.redirectURI(port: 49152),
                                               pkce: pkce, state: "st", loginHint: "owner@example.com")
        let c = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        XCTAssertEqual(c.host, "accounts.google.com")
        let q = Dictionary(uniqueKeysWithValues: (c.queryItems ?? []).map { ($0.name, $0.value ?? "") })
        XCTAssertEqual(q["client_id"], "cid.apps.googleusercontent.com")
        XCTAssertEqual(q["redirect_uri"], "http://127.0.0.1:49152/oauth2")
        XCTAssertEqual(q["response_type"], "code")
        XCTAssertEqual(q["scope"], "openid email")
        XCTAssertEqual(q["code_challenge"], pkce.challenge)
        XCTAssertEqual(q["code_challenge_method"], "S256")
        XCTAssertEqual(q["state"], "st")
        XCTAssertEqual(q["login_hint"], "owner@example.com")
    }

    func testRedirectParsing() {
        XCTAssertEqual(GoogleOAuth.parseRedirect(requestLine: "GET /oauth2?state=st&code=4%2Fabc&scope=email HTTP/1.1", expectedState: "st"), .code("4/abc"))
        XCTAssertEqual(GoogleOAuth.parseRedirect(requestLine: "GET /oauth2?state=other&code=4 HTTP/1.1", expectedState: "st"), .stateMismatch)
        XCTAssertEqual(GoogleOAuth.parseRedirect(requestLine: "GET /oauth2?error=access_denied&state=st HTTP/1.1", expectedState: "st"), .error("access_denied"))
        XCTAssertEqual(GoogleOAuth.parseRedirect(requestLine: "GET /favicon.ico HTTP/1.1", expectedState: "st"), .notARedirect)
        XCTAssertEqual(GoogleOAuth.parseRedirect(requestLine: "POST /oauth2 HTTP/1.1", expectedState: "st"), .notARedirect)
    }

    func testTokenRequestBodyHasNoSecretAndEncodes() throws {
        let body = String(decoding: GoogleOAuth.tokenRequestBody(clientId: "cid", code: "4/a b", redirectURI: "http://127.0.0.1:1/oauth2",
                                                                 pkce: GoogleOAuth.PKCE(verifier: "ver")), as: UTF8.self)
        XCTAssertEqual(body, "client_id=cid&code=4%2Fa%20b&code_verifier=ver&redirect_uri=http%3A%2F%2F127.0.0.1%3A1%2Foauth2&grant_type=authorization_code")
        XCTAssertFalse(body.contains("client_secret"))
        let withSecret = String(decoding: GoogleOAuth.tokenRequestBody(clientId: "cid", clientSecret: "s3", code: "c", redirectURI: "r",
                                                                       pkce: GoogleOAuth.PKCE(verifier: "v")), as: UTF8.self)
        XCTAssertTrue(withSecret.hasPrefix("client_id=cid&client_secret=s3&code=c&"))
        let t = try JSONDecoder().decode(GoogleOAuth.TokenResponse.self, from: Data(#"{"access_token":"ya29.x","expires_in":3599,"scope":"openid email","token_type":"Bearer"}"#.utf8))
        XCTAssertEqual(t.access_token, "ya29.x")
        XCTAssertNil(t.error)
    }
}
