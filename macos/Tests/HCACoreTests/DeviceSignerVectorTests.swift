import CryptoKit
import Foundation
import XCTest
@testable import HCACore

/// Asserts the shared contract in api/tests/vectors/devicesig_v1.json. The
/// server's pytest asserts the same file; if either side changes, both break.
final class DeviceSignerVectorTests: XCTestCase {
    struct Vectors: Decodable {
        struct Case: Decodable {
            let name, timestamp, nonce, method, path_and_query, body_utf8, body_sha256_hex, canonical_utf8, signature_b64url: String
        }
        let test_private_key_raw_hex: String
        let public_key_spki_pem: String
        let device_id: String
        let cases: [Case]
    }

    static func loadVectors() throws -> Vectors {
        // macos/Tests/HCACoreTests/<this file> -> repo root is three levels up from macos/.
        let here = URL(fileURLWithPath: #filePath)
        let repo = here.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let url = repo.appendingPathComponent("api/tests/vectors/devicesig_v1.json")
        return try JSONDecoder().decode(Vectors.self, from: Data(contentsOf: url))
    }

    static func data(fromHex hex: String) -> Data {
        var bytes = [UInt8](); var i = hex.startIndex
        while i < hex.endIndex {
            let j = hex.index(i, offsetBy: 2); bytes.append(UInt8(hex[i..<j], radix: 16)!); i = j
        }
        return Data(bytes)
    }

    func testDeviceIdAndPEMMatchVectors() throws {
        let v = try Self.loadVectors()
        let signer = try DeviceSigner(rawRepresentation: Self.data(fromHex: v.test_private_key_raw_hex))
        XCTAssertEqual(signer.deviceId, v.device_id)
        XCTAssertEqual(signer.publicKeyPEM.trimmingCharacters(in: .whitespacesAndNewlines),
                       v.public_key_spki_pem.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func testCanonicalStringsAndBodyHashes() throws {
        let v = try Self.loadVectors()
        for c in v.cases {
            let body = Data(c.body_utf8.utf8)
            XCTAssertEqual(DeviceSigner.sha256Hex(body), c.body_sha256_hex, c.name)
            XCTAssertEqual(DeviceSigner.canonicalString(timestamp: c.timestamp, nonce: c.nonce, method: c.method,
                                                        pathAndQuery: c.path_and_query, body: body), c.canonical_utf8, c.name)
        }
    }

    func testRecordedServerSignaturesVerify() throws {
        let v = try Self.loadVectors()
        for c in v.cases {
            XCTAssertTrue(DeviceSigner.verify(publicKeyPEM: v.public_key_spki_pem, canonical: c.canonical_utf8,
                                              signatureB64url: c.signature_b64url), c.name)
            XCTAssertFalse(DeviceSigner.verify(publicKeyPEM: v.public_key_spki_pem, canonical: c.canonical_utf8 + "x",
                                               signatureB64url: c.signature_b64url), c.name)
        }
    }

    func testOwnSignaturesVerifyWithVectorPublicKey() throws {
        let v = try Self.loadVectors()
        let signer = try DeviceSigner(rawRepresentation: Self.data(fromHex: v.test_private_key_raw_hex))
        for c in v.cases {
            let sig = try signer.sign(canonical: c.canonical_utf8)
            XCTAssertEqual(DeviceSigner.b64urlDecode(sig)?.count, 64, "raw r||s")
            XCTAssertFalse(sig.contains("="), "no padding")
            XCTAssertTrue(DeviceSigner.verify(publicKeyPEM: v.public_key_spki_pem, canonical: c.canonical_utf8, signatureB64url: sig), c.name)
        }
    }

    func testHeadersAndPathAndQuery() throws {
        let signer = DeviceSigner()
        let url = URL(string: "https://example.invalid/v1/audit?limit=5&since=2026-10-01T00%3A00%3A00Z")!
        XCTAssertEqual(DeviceSigner.pathAndQuery(of: url), "/v1/audit?limit=5&since=2026-10-01T00%3A00%3A00Z")
        XCTAssertEqual(DeviceSigner.pathAndQuery(of: URL(string: "https://example.invalid/v1/inbox")!), "/v1/inbox")
        let h = try signer.headers(method: "get", url: url, body: Data(), now: Date(timeIntervalSince1970: 1759600001), nonce: "n")
        XCTAssertEqual(h["X-HCA-Device"], signer.deviceId)
        XCTAssertEqual(h["X-HCA-Timestamp"], "1759600001")
        XCTAssertEqual(h["X-HCA-Nonce"], "n")
        let canonical = DeviceSigner.canonicalString(timestamp: "1759600001", nonce: "n", method: "GET",
                                                     pathAndQuery: "/v1/audit?limit=5&since=2026-10-01T00%3A00%3A00Z", body: Data())
        XCTAssertTrue(DeviceSigner.verify(publicKeyPEM: signer.publicKeyPEM, canonical: canonical, signatureB64url: h["X-HCA-Signature"]!))
        let req = try signer.signedRequest(method: "post", url: URL(string: "https://example.invalid/v1/x")!, body: Data("{}".utf8))
        XCTAssertEqual(req.httpMethod, "POST")
        XCTAssertEqual(req.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertNotNil(req.value(forHTTPHeaderField: "X-HCA-Signature"))
    }
}
