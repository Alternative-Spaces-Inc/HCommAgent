import Foundation
import ServiceManagement
import HCACore

/// HCA-P18 Path B: a user LaunchAgent at ~/Library/LaunchAgents/<label>.plist
/// with the absolute path of this bundle's executable, loaded and unloaded with
/// launchctl in the gui/<uid> domain. One agent gives launch at login
/// (RunAtLoad) and relaunch after a crash or kill (KeepAlive SuccessfulExit
/// false) while a clean Quit stays quit. Default off: nothing is written until
/// the owner ticks the menu row. The earlier bundled SMAppService agent (Day 1)
/// is unregistered once if still present; see LaunchAgentPlist for why.
enum LoginItem {
    static let label = LaunchAgentPlist.label
    static let home = FileManager.default.homeDirectoryForCurrentUser.path
    static var plistPath: String { LaunchAgentPlist.path(home: home) }
    static var program: String { Bundle.main.executablePath ?? "" }
    static var domain: String { "gui/\(getuid())" }
    static var target: String { domain + "/" + label }

    enum State: Equatable {
        case off, on, requiresApproval, notFound
    }

    /// on: plist present and the job loaded in launchd. requiresApproval: plist
    /// present but launchd has it disabled (System Settings, Allow in the
    /// Background, switch off). notFound: plist present but the job is not
    /// loaded (bootstrap pending or failed). off: no plist.
    static var state: State {
        guard FileManager.default.fileExists(atPath: plistPath) else { return .off }
        if isDisabled() { return .requiresApproval }
        return launchctl(["print", target]).status == 0 ? .on : .notFound
    }

    static func menuTitle(_ s: State) -> String {
        switch s {
        case .off: return "Launch at login"
        case .on: return "Launch at login"
        case .requiresApproval: return "Launch at login: allow in System Settings (click opens it)"
        case .notFound: return "Launch at login: agent not loaded (click to load)"
        }
    }

    /// Write the plist for this executable and load it. launchd starts the agent
    /// instance at once (RunAtLoad); the caller hands over.
    static func register() -> (State, String?) {
        dropBundledRegistration()
        let dir = (plistPath as NSString).deletingLastPathComponent
        do {
            try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try LaunchAgentPlist.contents(program: program).write(toFile: plistPath, atomically: true, encoding: .utf8)
        } catch {
            return (state, "plist write failed: \(error.localizedDescription)")
        }
        _ = launchctl(["enable", target])
        let r = launchctl(["bootstrap", domain, plistPath])
        if r.status != 0, r.status != 37 {  // 37: already bootstrapped
            return (state, "launchctl bootstrap exit \(r.status): \(r.output.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
        return (state, nil)
    }

    /// Unload the job and remove the plist. From the launchd instance this ends
    /// the process; the owner reopens the app from the bundle.
    static func unregister() -> (State, String?) {
        let r = launchctl(["bootout", target])
        try? FileManager.default.removeItem(atPath: plistPath)
        if r.status != 0, r.status != 3, r.status != 113 {  // 3 / 113: no such job
            return (state, "launchctl bootout exit \(r.status): \(r.output.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
        return (state, nil)
    }

    /// Load a present plist whose job is not loaded (after a logout, or when a
    /// hand-opened instance finds the agent configured but not running).
    static func load() -> (State, String?) {
        let r = launchctl(["bootstrap", domain, plistPath])
        if r.status != 0, r.status != 37 {
            return (state, "launchctl bootstrap exit \(r.status): \(r.output.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
        return (state, nil)
    }

    /// Restart the loaded job on the current binary (after a rebuild).
    static func kickstart() -> (State, String?) {
        let r = launchctl(["kickstart", "-k", target])
        return (state, r.status == 0 ? nil : "launchctl kickstart exit \(r.status)")
    }

    /// Flip the registration.
    static func toggle() -> (State, String?) {
        switch state {
        case .off: return register()
        case .on: return unregister()
        case .notFound: return load()
        case .requiresApproval:
            openLoginItemsSettings()
            return (state, nil)
        }
    }

    static func openLoginItemsSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.LoginItems-Settings.extension") {
            NSWorkspace.shared.open(url)
        }
    }

    /// Day 1 registered a bundled agent through SMAppService; it never spawns
    /// (empty cdhash requirement without a Team ID). Remove it once.
    static func dropBundledRegistration() {
        let s = SMAppService.agent(plistName: LaunchAgentPlist.fileName)
        if s.status == .enabled || s.status == .requiresApproval {
            try? s.unregister()
        }
    }

    static func isDisabled() -> Bool {
        let r = launchctl(["print-disabled", domain])
        return r.output.split(separator: "\n").contains { line in
            line.contains("\"\(label)\"") && line.contains("disabled")
        }
    }

    struct Result { let status: Int32; let output: String }

    static func launchctl(_ args: [String]) -> Result {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return Result(status: -1, output: error.localizedDescription) }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return Result(status: p.terminationStatus, output: String(decoding: data, as: UTF8.self))
    }
}

import AppKit
