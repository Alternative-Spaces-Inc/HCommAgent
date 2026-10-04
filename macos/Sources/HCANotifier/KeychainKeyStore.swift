import Foundation
import HCACore
import Security

/// Keeps the device's P-256 private scalar in the login Keychain as a
/// generic password item (service = bundle id, account = "device-key").
/// Created on first use, never exported by the app; only the public key
/// leaves the Mac (for POST /v1/devices, done by the owner with the admin
/// token).
enum KeychainKeyStore {
    static let service = Bundle.main.bundleIdentifier ?? "com.alternativespaces.hca.notifier"
    static let account = "device-key"

    enum StoreError: Error, LocalizedError {
        case osStatus(OSStatus, String)
        var errorDescription: String? {
            if case .osStatus(let s, let op) = self {
                return "Keychain \(op) failed: \(SecCopyErrorMessageString(s, nil) as String? ?? String(s))"
            }
            return nil
        }
    }

    private static var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    /// Loads the signer, creating and storing a fresh key when none exists.
    static func loadOrCreate() throws -> DeviceSigner {
        if let raw = try load() {
            return try DeviceSigner(rawRepresentation: raw)
        }
        let signer = DeviceSigner()
        try store(signer.privateKey.rawRepresentation)
        return signer
    }

    static func load() throws -> Data? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw StoreError.osStatus(status, "read") }
        return item as? Data
    }

    static func store(_ raw: Data) throws {
        var query = baseQuery
        query[kSecValueData as String] = raw
        query[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        query[kSecAttrLabel as String] = "HCA Notifier device key"
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw StoreError.osStatus(status, "add") }
    }
}
