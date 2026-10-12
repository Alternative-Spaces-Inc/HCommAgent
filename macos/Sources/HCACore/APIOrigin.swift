import Foundation

/// A trust boundary: saved settings are a single origin, never an arbitrary URL.
public struct APIOrigin: Equatable, Sendable {
    public enum ValidationError: Error { case invalidOrigin, invalidPathComponent }
    public let url: URL

    public init(_ value: String, allowLocalHTTP: Bool = false) throws {
        guard value == value.trimmingCharacters(in: .whitespacesAndNewlines),
              let c = URLComponents(string: value),
              let scheme = c.scheme?.lowercased(), let host = c.host?.lowercased(),
              !host.isEmpty, c.user == nil, c.password == nil,
              c.query == nil, c.fragment == nil, c.path.isEmpty || c.path == "/",
              c.port == nil || (1...65535).contains(c.port!),
              scheme == "https" || (allowLocalHTTP && scheme == "http" && ["localhost", "127.0.0.1", "[::1]"].contains(host)),
              let url = c.url else { throw ValidationError.invalidOrigin }
        var normalized = c
        normalized.scheme = scheme; normalized.host = host; normalized.path = ""
        guard let normalizedURL = normalized.url, normalizedURL.host == url.host else { throw ValidationError.invalidOrigin }
        self.url = normalizedURL
    }

    public func endpoint(_ components: [String]) throws -> URL {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        let encoded = try components.map { component -> String in
            guard !component.isEmpty, component != ".", component != "..",
                  let result = component.addingPercentEncoding(withAllowedCharacters: allowed) else {
                throw ValidationError.invalidPathComponent
            }
            return result
        }
        var result = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        result.percentEncodedPath = "/" + encoded.joined(separator: "/")
        return result.url!
    }
}

/// A signature applies to exactly one URL. Even same-origin redirects are refused.
public final class NoRedirectDelegate: NSObject, URLSessionTaskDelegate {
    public func urlSession(_ session: URLSession, task: URLSessionTask,
                           willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                           completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
