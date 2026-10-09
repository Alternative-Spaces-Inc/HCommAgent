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

/// HCA-P18 Path B (Day 2 selection, 2026-10-09): a user LaunchAgent written by the
/// app to ~/Library/LaunchAgents/<label>.plist with the absolute executable path,
/// loaded with launchctl bootstrap gui/<uid>. Chosen after the bundled
/// SMAppService agent failed to spawn (EX_CONFIG): the P16 identity is
/// self-signed with no Team ID, so Apple's service pinned an empty cdhash
/// requirement that no binary satisfies. Pure rendering; the file write and the
/// launchctl calls live in the app target.
public enum LaunchAgentPlist {
    public static let label = "com.alternativespaces.hca.notifier"
    public static let fileName = label + ".plist"
    /// Set by launchd for the agent instance (EnvironmentVariables in the plist), so
    /// the app can tell the launchd-managed instance from one opened by hand.
    public static let launchdMarker = "HCA_LAUNCHD_AGENT"

    /// Registering the agent from a hand-opened instance makes launchd start a
    /// second instance at once (RunAtLoad), so two menu bar icons appear. Rule for
    /// a starting instance that finds another one running: the launchd instance
    /// wins (it is the one launchd restarts after a crash); a hand-opened instance
    /// exits with status 0.
    public enum DuplicateAction: Equatable { case none, exitSelf, terminateOther }
    public static func duplicateAction(otherRunning: Bool, selfIsLaunchd: Bool) -> DuplicateAction {
        guard otherRunning else { return .none }
        return selfIsLaunchd ? .terminateOther : .exitSelf
    }

    /// Where the agent plist lives for a given home directory.
    public static func path(home: String) -> String {
        home + "/Library/LaunchAgents/" + fileName
    }

    /// The plist text for an executable at `program` (absolute path). RunAtLoad
    /// starts it at login; KeepAlive SuccessfulExit false relaunches after a
    /// crash or kill while a clean Quit (exit 0) stays quit.
    public static func contents(program: String) -> String {
        """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict>
          <key>Label</key><string>\(label)</string>
          <key>Program</key><string>\(xmlEscape(program))</string>
          <key>RunAtLoad</key><true/>
          <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
          <key>ProcessType</key><string>Interactive</string>
          <key>EnvironmentVariables</key><dict><key>\(launchdMarker)</key><string>1</string></dict>
        </dict></plist>

        """
    }

    static func xmlEscape(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;")
         .replacingOccurrences(of: "<", with: "&lt;")
         .replacingOccurrences(of: ">", with: "&gt;")
    }

    /// Interpret `launchctl print gui/<uid>/<label>` output: nil when the job is
    /// not loaded (launchctl exits non-zero), else the pid when running.
    public static func runningPid(launchctlPrint output: String?) -> Int? {
        guard let output = output else { return nil }
        for line in output.split(separator: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            if t.hasPrefix("pid = ") { return Int(t.dropFirst(6)) }
        }
        return nil
    }
}
