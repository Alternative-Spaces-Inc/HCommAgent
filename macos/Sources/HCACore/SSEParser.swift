import Foundation

/// Incremental text/event-stream parser. Feed raw chunks; collect events.
/// Handles CRLF and LF, multi-line data, comments (": keepalive") and
/// events split across chunks.
public struct SSEEvent: Equatable {
    public let event: String
    public let data: String
    public let id: String?
}

public final class SSEParser {
    private var buffer = ""
    private var eventName = ""
    private var dataLines: [String] = []
    private var lastId: String?

    public init() {}

    public func feed(_ chunk: Data) -> [SSEEvent] {
        buffer += String(decoding: chunk, as: UTF8.self)
        return drain()
    }

    public func feed(line: String) -> [SSEEvent] {
        buffer += line + "\n"
        return drain()
    }

    private func drain() -> [SSEEvent] {
        var out: [SSEEvent] = []
        buffer = buffer.replacingOccurrences(of: "\r\n", with: "\n")
        while let nl = buffer.firstIndex(of: "\n") {
            let line = String(buffer[buffer.startIndex..<nl])
            buffer.removeSubrange(buffer.startIndex...nl)
            if line.isEmpty {
                if !dataLines.isEmpty || !eventName.isEmpty {
                    out.append(SSEEvent(event: eventName.isEmpty ? "message" : eventName,
                                        data: dataLines.joined(separator: "\n"), id: lastId))
                }
                eventName = ""; dataLines = []
                continue
            }
            if line.hasPrefix(":") { continue }
            let (field, value) = split(line)
            switch field {
            case "event": eventName = value
            case "data": dataLines.append(value)
            case "id": lastId = value
            default: break
            }
        }
        return out
    }

    private func split(_ line: String) -> (String, String) {
        guard let colon = line.firstIndex(of: ":") else { return (line, "") }
        let field = String(line[line.startIndex..<colon])
        var value = String(line[line.index(after: colon)...])
        if value.hasPrefix(" ") { value.removeFirst() }
        return (field, value)
    }
}
