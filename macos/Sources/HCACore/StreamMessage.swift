import Foundation

public enum StreamMessage: Sendable {
    case reset
    case revoked
    case envelope(StreamEnvelope, String?)
    case ignored

    /// Control events can have raw reason JSON or a normal envelope.
    public static func decode(_ event: SSEEvent) -> StreamMessage {
        if event.event == "stream.reset" { return .reset }
        if event.event == "stream.closed" {
            if let object = try? JSONSerialization.jsonObject(with: Data(event.data.utf8)) as? [String: Any],
               let reason = object["reason"] as? String, reason.lowercased().contains("revoked") { return .revoked }
            return .ignored
        }
        guard let envelope = try? JSONDecoder().decode(StreamEnvelope.self, from: Data(event.data.utf8)) else { return .ignored }
        return .envelope(envelope, event.id)
    }
}
