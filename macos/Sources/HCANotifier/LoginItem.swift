import Foundation
import ServiceManagement
import HCACore

/// HCA-P18: the bundled LaunchAgent (Contents/Library/LaunchAgents/<label>.plist,
/// written by scripts/bundle.sh) registered through SMAppService. One agent
/// gives launch at login (RunAtLoad) and relaunch after a crash or kill
/// (KeepAlive SuccessfulExit false) while a clean Quit stays quit. Default off:
/// nothing registers until the owner ticks the menu row.
enum LoginItem {
    static let service = SMAppService.agent(plistName: LaunchAgentPlist.fileName)

    enum State: Equatable {
        case off, on, requiresApproval, notFound
    }

    static var state: State {
        switch service.status {
        case .enabled: return .on
        case .requiresApproval: return .requiresApproval
        case .notFound: return .notFound
        case .notRegistered: return .off
        @unknown default: return .off
        }
    }

    static func menuTitle(_ s: State) -> String {
        switch s {
        case .off: return "Launch at login"
        case .on: return "Launch at login"
        case .requiresApproval: return "Launch at login: allow in System Settings (click opens it)"
        case .notFound: return "Launch at login: agent plist missing from the bundle"
        }
    }

    /// Re-register after a rebuild. launchd pins the agent to the bundle version
    /// and code requirement it saw at registration (launchctl print: "parent
    /// bundle version", "has LWCR"); after bundle.sh replaces the app the spawn
    /// fails with EX_CONFIG until the registration is refreshed (Day 2 finding).
    static func reregister() -> (State, String?) {
        do {
            if state == .on { try service.unregister() }
            try service.register()
            return (state, nil)
        } catch {
            return (state, error.localizedDescription)
        }
    }

    /// Flip the registration. Returns the state after the call and the error text if any.
    static func toggle() -> (State, String?) {
        do {
            switch state {
            case .on:
                try service.unregister()
            case .requiresApproval:
                SMAppService.openSystemSettingsLoginItems()
            case .off, .notFound:
                try service.register()
            }
            return (state, nil)
        } catch {
            return (state, error.localizedDescription)
        }
    }
}
