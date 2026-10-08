import Foundation

/// HCA-P18 liveness: when did this device last read its inbox successfully
/// (poll or stream event) and is the stream up. Pure state plus rendering so
/// the staleness rule is unit tested; the app owns the clock and the
/// reconnect side effect.
public struct Liveness: Equatable {
    public var lastRead: Date?
    public var streamConnected: Bool
    /// Stale after three poll intervals without a successful read.
    public static let staleAfter: TimeInterval = 180

    public init(lastRead: Date? = nil, streamConnected: Bool = false) {
        self.lastRead = lastRead
        self.streamConnected = streamConnected
    }

    public mutating func recordRead(at now: Date) { lastRead = now }

    /// True once a read happened and it is older than staleAfter. No read yet
    /// is reported as "waiting", not stale, so a fresh launch does not force a
    /// reconnect before the first poll had a chance.
    public func isStale(now: Date) -> Bool {
        guard let last = lastRead else { return false }
        return now.timeIntervalSince(last) >= Self.staleAfter
    }

    public func title(now: Date) -> String {
        let stream = streamConnected ? "stream connected" : "stream down, polling"
        guard let last = lastRead else { return "Last inbox read: waiting for the first read, \(stream)" }
        let age = Self.ageText(now.timeIntervalSince(last))
        return isStale(now: now) ? "Last inbox read: \(age), stale" : "Last inbox read: \(age), \(stream)"
    }

    /// "12 s ago", "6 min ago", "2 h ago".
    public static func ageText(_ seconds: TimeInterval) -> String {
        let s = max(0, Int(seconds.rounded(.down)))
        if s < 60 { return "\(s) s ago" }
        if s < 3600 { return "\(s / 60) min ago" }
        return "\(s / 3600) h ago"
    }
}

/// HCA-P18: the LaunchAgent plist shipped inside the bundle and registered
/// through SMAppService.agent(plistName:). Rendered here so a test can parse
/// it and bundle.sh can print it (swift run is not used by bundle.sh; the
/// script carries the same text, and the test guards both against drift by
/// comparing with macos/scripts/launchagent.plist).
public enum LaunchAgentPlist {
    public static let label = "com.alternativespaces.hca.notifier"
    public static let fileName = label + ".plist"
    public static let bundleProgram = "Contents/MacOS/HCANotifier"

    public static func contents() -> String {
        """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict>
          <key>Label</key><string>\(label)</string>
          <key>BundleProgram</key><string>\(bundleProgram)</string>
          <key>RunAtLoad</key><true/>
          <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
          <key>ProcessType</key><string>Interactive</string>
        </dict></plist>

        """
    }
}
