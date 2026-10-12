import Foundation

/// TCP may split the browser's request line at any byte; accept a bounded, complete CRLF line only.
public struct LoopbackRequestLine: Sendable {
    public enum Result: Equatable { case pending, line(String), rejected }
    public static let maximumBytes = 8192
    private var data = Data()
    public init() {}

    public mutating func append(_ bytes: Data, complete: Bool = false) -> Result {
        data.append(bytes)
        if let end = data.range(of: Data([13, 10])) {
            guard end.lowerBound <= Self.maximumBytes,
                  let line = String(data: data[..<end.lowerBound], encoding: .utf8),
                  !line.contains("\n"), !line.contains("\r"), !line.contains("\0") else { return .rejected }
            return .line(line)
        }
        if data.count > Self.maximumBytes || complete { return .rejected }
        return .pending
    }
}
