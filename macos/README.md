# HCA Notifier (macOS)

Rung 3 of the surface ladder: native macOS notifications with action
buttons for pending asks, on the same API as the Chrome extension. Plan:
docs/plans/HCA_P13_MACOS_NOTIFICATION_v1.md.

Build on a Mac with Xcode (license accepted):

    bash macos/scripts/bundle.sh          # writes ~/hca-notifier/HCA Notifier.app
    open ~/hca-notifier/HCA\ Notifier.app

Tests (pure logic, no bundle needed):

    cd macos && swift test

Notifications will not post from the bare executable; the bundle is required.

## Signing (HCA-P16)

bundle.sh signs with the local identity "HCA Notifier Local Signing" when it
exists, else ad-hoc. Create it once:

    bash macos/scripts/make_signing_identity.sh

Two macOS dialogs appear, one on this script and one on the next bundle.sh:

1. "security wants to make changes": admin password, then Modify Settings
   (trusts the self-signed certificate for code signing).
2. "codesign wants to sign using key HCA Notifier Local Signing in your
   keychain": click Always Allow (lets codesign use the key silently).

A certificate identity gives the app one designated requirement across
rebuilds, so the Keychain's device-key item asks once more after the first
re-signed launch (Always Allow) and never again. Check the identity with
`security find-identity -v -p codesigning`; force one with HCA_SIGN_IDENTITY.
Developer ID and notarization are not used: the app runs only on the Mac
that builds it.

## Launch at login and liveness (HCA-P18)

Nothing registers by itself. Menu row "Launch at login" (above "Copy device
public key") writes `~/Library/LaunchAgents/com.alternativespaces.hca.notifier.plist`
(rendered by `LaunchAgentPlist.contents(program:)`, asserted by
LivenessTests) with `Program` set to this bundle's executable path, then
runs `launchctl bootstrap gui/<uid> <plist>`. Ticked means the app starts
at login (RunAtLoad) and is relaunched after a crash or kill (KeepAlive,
SuccessfulExit false); "Quit HCA Notifier" (exit 0) stays quit. macOS lists
it under System Settings, General, Login Items and Extensions, "Allow in
the Background"; if the row reads "allow in System Settings", clicking it
opens that pane. Unticking runs `launchctl bootout` and removes the plist,
which stops the launchd job: the app quits and is reopened from the bundle
by hand.

Why a user LaunchAgent and not the bundled SMAppService agent (Day 2 finding,
2026-10-09): the Day 1 agent registered, spawned once, and then failed every
start with `last exit code = 78: EX_CONFIG`, job state "spawn failed" (44
attempts overnight). `launchctl print` showed the registration's
lightweight code requirement as `cdhash $in [ ]`, an empty list no binary
satisfies. The P16 identity is self-signed (`TeamIdentifier=not set`), and
Apple's service builds a usable requirement only from a Team ID. A Team ID
(Apple Development certificate via an Apple ID in Xcode) would allow the
bundled agent again; until then Path B. The app unregisters a leftover
bundled registration the first time the row is ticked.

One instance only (Day 2 finding: registering from a hand-opened instance
made launchd start a second one, two icons). The plist sets
HCA_LAUNCHD_AGENT=1 for the launchd instance; on start, a launchd instance
terminates any hand-opened one and a hand-opened instance exits when another
is running. After a successful registration the hand-opened instance hands
over to launchd within 2 s.

After a rebuild (`bundle.sh` replaces the app in place, same path) the
loaded job restarts on the new binary with

    launchctl kickstart -k gui/$(id -u)/com.alternativespaces.hca.notifier

or by opening the bundle by hand once: a hand-opened instance that finds the
agent configured but not running kickstarts it (or loads a present plist
after a logout) and hands over within 2 s.

Menu row "Last inbox read: 12 s ago, stream connected" is the liveness
line (updated when the menu opens, after each poll and every 30 s). After
180 s without a successful inbox read it reads "stale", the status line
reads "Status: stale, reconnecting", the stream and poll tasks are
restarted and the transition is in the unified log (subsystem
com.alternativespaces.hca.notifier, category liveness):

    log show --last 10m --predicate 'subsystem == "com.alternativespaces.hca.notifier" AND category == "liveness"'

Inspect the agent: `launchctl print gui/$(id -u)/com.alternativespaces.hca.notifier`.
