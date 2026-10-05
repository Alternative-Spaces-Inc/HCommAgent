import Foundation

/// HCA-P14 priority controls: the ladder places the owner can pick from the
/// menu. The raw values are the server's devices.priority numbers, lower is
/// earlier; they match the extension options page (extension/lib/api.js).
public enum SurfacePriority: Int, CaseIterable {
    case primary = 10
    case standard = 100
    case fallback = 200

    public var title: String {
        switch self {
        case .primary: return "Primary: asks show here first"
        case .standard: return "Default: shares the ladder with other default devices"
        case .fallback: return "Fallback: asks reach here only after the primary's window"
        }
    }

    /// Menu line for a server value, including values outside the three places.
    public static func label(for priority: Int?) -> String {
        guard let p = priority else { return "Priority: unknown" }
        if let place = SurfacePriority(rawValue: p) { return "Priority: \(place.title.split(separator: ":").first ?? "") (\(p))" }
        return "Priority: custom (\(p))"
    }
}
