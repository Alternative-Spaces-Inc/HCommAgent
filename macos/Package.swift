// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "HCANotifier",
    platforms: [.macOS(.v14)],
    targets: [
        // Pure logic, no AppKit: testable on any Mac without a bundle.
        .target(name: "HCACore"),
        // The menu bar app. Needs the .app bundle from scripts/bundle.sh to
        // post notifications (UNUserNotificationCenter refuses bare binaries).
        .executableTarget(name: "HCANotifier", dependencies: ["HCACore"]),
        .testTarget(name: "HCACoreTests", dependencies: ["HCACore"]),
    ]
)
