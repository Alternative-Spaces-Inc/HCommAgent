import CryptoKit
import Foundation

/// Per-device ECDSA P-256 request signing for the human surface routes.
/// Mirrors api/app/devicesig.py and extension/lib/devicekey.js exactly; the
/// shared vectors in api/tests/vectors/devicesig_v1.json pin the contract.
///
/// Headers: X-HCA-Device, X-HCA-Timestamp, X-HCA-Nonce, X-HCA-Signature.
/// Canonical string:
///   "HCA1\n" + timestamp + "\n" + nonce + "\n" + METHOD + "\n" + path_and_query + "\n" + sha256hex(body)
/// Signature: base64url, no padding, of the raw 64-byte r||s.
public struct DeviceSigner {
    public static let prefix = "HCA1"
    public static let headerDevice = "X-HCA-Device"
    public static let headerTimestamp = "X-HCA-Timestamp"
    public static let headerNonce = "X-HCA-Nonce"
    public static let headerSignature = "X-HCA-Signature"

    public let privateKey: P256.Signing.PrivateKey

    public init(privateKey: P256.Signing.PrivateKey) {
        self.privateKey = privateKey
    }

    public init() {
        self.privateKey = P256.Signing.PrivateKey()
    }

    /// 32-byte scalar, what the Keychain stores.
    public init(rawRepresentation: Data) throws {
        self.privateKey = try P256.Signing.PrivateKey(rawRepresentation: rawRepresentation)
    }

    public var publicKey: P256.Signing.PublicKey { privateKey.publicKey }

    /// SubjectPublicKeyInfo PEM, the body of POST /v1/devices public_key_pem.
    public var publicKeyPEM: String { publicKey.pemRepresentation + "\n" }

    /// dev_ + first 32 hex chars of SHA-256 over the SPKI DER.
    public var deviceId: String { Self.deviceId(forSPKIDER: publicKey.derRepresentation) }

    public static func deviceId(forSPKIDER der: Data) -> String {
        "dev_" + String(hexOf(SHA256.hash(data: der)).prefix(32))
    }

    public static func sha256Hex(_ body: Data) -> String {
        hexOf(SHA256.hash(data: body))
    }

    public static func canonicalString(timestamp: String, nonce: String, method: String, pathAndQuery: String, body: Data) -> String {
        [prefix, timestamp, nonce, method.uppercased(), pathAndQuery, sha256Hex(body)].joined(separator: "\n")
    }

    /// Path plus "?" and the raw query when present, as the server sees it.
    public static func pathAndQuery(of url: URL) -> String {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url.path }
        var path = components.percentEncodedPath
        if path.isEmpty { path = "/" }
        if let query = components.percentEncodedQuery, !query.isEmpty {
            return path + "?" + query
        }
        return path
    }

    public static func b64url(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    public static func b64urlDecode(_ s: String) -> Data? {
        var b = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while b.count % 4 != 0 { b += "=" }
        return Data(base64Encoded: b)
    }

    /// Signs a canonical string; returns the base64url r||s.
    public func sign(canonical: String) throws -> String {
        let sig = try privateKey.signature(for: Data(canonical.utf8))
        return Self.b64url(sig.rawRepresentation)
    }

    /// Verifies a base64url r||s signature against a public key (used by the
    /// vector tests; the server is the real verifier).
    public static func verify(publicKeyPEM: String, canonical: String, signatureB64url: String) -> Bool {
        guard let pub = try? P256.Signing.PublicKey(pemRepresentation: publicKeyPEM),
              let raw = b64urlDecode(signatureB64url),
              let sig = try? P256.Signing.ECDSASignature(rawRepresentation: raw) else { return false }
        return pub.isValidSignature(sig, for: Data(canonical.utf8))
    }

    /// The four headers for one request. `now` and `nonce` are injectable for tests.
    public func headers(method: String, url: URL, body: Data,
                        now: Date = Date(), nonce: String = UUID().uuidString.lowercased()) throws -> [String: String] {
        let timestamp = String(Int(now.timeIntervalSince1970))
        let canonical = Self.canonicalString(timestamp: timestamp, nonce: nonce, method: method,
                                             pathAndQuery: Self.pathAndQuery(of: url), body: body)
        return [
            Self.headerDevice: deviceId,
            Self.headerTimestamp: timestamp,
            Self.headerNonce: nonce,
            Self.headerSignature: try sign(canonical: canonical),
        ]
    }

    /// A URLRequest with the signature headers and a JSON content type when a body is present.
    public func signedRequest(method: String, url: URL, body: Data? = nil, extraHeaders: [String: String] = [:]) throws -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method.uppercased()
        let bodyData = body ?? Data()
        for (k, v) in try headers(method: method, url: url, body: bodyData) { request.setValue(v, forHTTPHeaderField: k) }
        for (k, v) in extraHeaders { request.setValue(v, forHTTPHeaderField: k) }
        if body != nil {
            request.httpBody = bodyData
            if request.value(forHTTPHeaderField: "Content-Type") == nil {
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            }
        }
        return request
    }

    private static func hexOf<D: Sequence>(_ digest: D) -> String where D.Element == UInt8 {
        digest.map { String(format: "%02x", $0) }.joined()
    }
}
