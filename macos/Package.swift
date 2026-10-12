// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "HCANotifier",
    platforms: [.macOS(.v14)],
    targets: [
        // Pure logic, no AppKit: testable on any Mac without a bundle.
        .target(name: "HCACore", swiftSettings: [.unsafeFlags(["-strict-concurrency=complete", "-warnings-as-errors"])]),
        // The menu bar app. Needs the .app bundle from scripts/bundle.sh to
        // post notifications (UNUserNotificationCenter refuses bare binaries).
        .executableTarget(name: "HCANotifier", dependencies: ["HCACore"], swiftSettings: [.unsafeFlags(["-strict-concurrency=complete", "-warnings-as-errors"])]),
        .testTarget(name: "HCACoreTests", dependencies: ["HCACore"], swiftSettings: [.unsafeFlags(["-strict-concurrency=complete", "-warnings-as-errors"])]),
        .testTarget(name: "HCANotifierTests", dependencies: ["HCANotifier", "HCACore"], swiftSettings: [.unsafeFlags(["-strict-concurrency=complete", "-warnings-as-errors"])]),
    ]
)
