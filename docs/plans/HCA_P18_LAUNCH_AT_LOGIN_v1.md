# HCA P18: HCA Notifier launch at login and liveness, plan v1 (2026-10-08)

## Why this and why now

HCA Notifier is the primary surface (P14 ladder: Notifier priority 10,
Chrome 200). Today nothing starts it. After a reboot, a logout, a crash
or a Quit the menu bar icon is gone and nothing says so: every ask waits
the 90 s fallback window (45 s for red gates) and then lands in Chrome,
which is exactly the silent degradation the ladder was built to avoid.
The owner also has no way to tell from the menu whether the surface is
alive beyond the "Server:" line, which only changes on the next event.
The owner selected this packet on 2026-10-08 10:4x PT over quiet hours,
free text on the Mac and Developer ID signing, while the Chrome Web Store
item (P17) is in review.

## Decision: one launchd agent registered through SMAppService

macOS 13 and later lets an app register a LaunchAgent plist shipped
inside its own bundle (`Contents/Library/LaunchAgents/<label>.plist`)
with `SMAppService.agent(plistName:)`. The agent appears in System
Settings, General, Login Items and Extensions as a background item the
owner can switch off, which is the system-blessed place for this. One
plist gives both behaviours: `RunAtLoad` starts the app at login and
`KeepAlive` with `SuccessfulExit false` relaunches it after a crash or a
kill while leaving a clean Quit alone. The alternative `SMAppService.
mainApp` (a plain login item) has no relaunch; a hand-written plist in
`~/Library/LaunchAgents` works but bypasses the Login Items UI and needs
`launchctl bootstrap` from the Terminal. Path B, used only if the bundled
agent is refused because the bundle lives in `~/hca-notifier` rather than
`/Applications`: bundle.sh writes the same plist to
`~/Library/LaunchAgents` and bootstraps it. Whether the refusal happens
is unverified and is the Day 2 selection point.

Default is off. Nothing registers until the owner ticks the menu row, so
a rebuild for another phase never adds a background item by itself.

## What ships

Mac app and bundle script only; no server change, no new credential, no
spend.

1. `macos/scripts/bundle.sh`: writes
   `Contents/Library/LaunchAgents/com.alternativespaces.hca.notifier.plist`
   (Label, `BundleProgram` `Contents/MacOS/HCANotifier`, `RunAtLoad`
   true, `KeepAlive` dict `SuccessfulExit` false, `ProcessType`
   Interactive) before signing, so the signature covers it.
2. `macos/Sources/HCANotifier/LoginItem.swift`: wraps
   `SMAppService.agent(plistName:)`: `status`, `register()`,
   `unregister()`, mapped to the four SMAppService states (not
   registered, enabled, requires approval, not found) as a menu title.
3. Menu row "Launch at login" with a checkmark that mirrors `status` on
   every menu open; clicking toggles register or unregister; when the
   state is "requires approval" the row reads "Launch at login: allow in
   System Settings" and clicking opens
   `SMAppService.openSystemSettingsLoginItems()`.
4. Liveness in `HCACore/Liveness.swift` (pure, tested): records the time
   of the last successful inbox read (poll or stream event) and the
   stream state; `title(now:)` renders "Last inbox read: 12 s ago,
   stream connected" or "Last inbox read: 6 min ago, stale"; `isStale`
   when the last read is older than three poll intervals (180 s). A menu
   row shows the title (refreshed on menu open and after each poll); when
   stale the app cancels and restarts the stream task and polls at once,
   and the status line reads "Status: stale, reconnecting". Each
   transition is logged to the unified log (subsystem
   com.alternativespaces.hca.notifier, category liveness).
5. `CFBundleShortVersionString` becomes 0.2.0 (first version with a
   background item); `CFBundleVersion` stays the commit.
6. `macos/README.md` and `docs/runbooks/HCA_FIRST_DEPLOY.md`: the
   "Launch at login" row, the Login Items path, and the Path B block.

Tests (XCTest, HCACoreTests): Liveness title and staleness at the
boundaries (0 s, 179 s, 180 s, no read yet); plist contents asserted
from the bundle script's template (label, BundleProgram, RunAtLoad,
KeepAlive shape) by a test that renders the template to a temp file and
parses it with PropertyListSerialization.

## Proof

Day 2, on the owner's Mac, each step read by the agent through the bridge
and the unified log, the owner only clicking where named:

1. Rebuild with bundle.sh (signed with the P16 identity, no Keychain
   prompt), relaunch, menu shows "Launch at login" unticked.
2. Owner ticks the row. macOS posts "Background Items Added". Agent reads
   `launchctl print gui/<uid>/com.alternativespaces.hca.notifier`: state
   running, program path inside the bundle.
3. Crash proof: agent runs `kill -9 <pid>` from the bridge. Within 10 s a
   new pid is running (launchctl print), the icon is back, the device's
   `last_seen_at` on the server advances (admin list via
   scripts/store_device.sh list). Log shows the liveness line "started".
4. Login proof: owner logs out (Apple menu, "Log Out <name>...", button
   "Log Out") and back in, or restarts the Mac. The agent reads
   `launchctl print` and the unified log: HCA Notifier started within 15 s
   of login with no click. A proof ask posted under a temporary key is
   answered from the banner (surface macos_notification) and the key is
   revoked after the read.
5. Clean Quit proof: owner picks "Quit HCA Notifier"; launchctl shows the
   service not running and no relaunch after 30 s (SuccessfulExit false
   honoured). Owner relaunches from the Finder or the agent via
   `launchctl kickstart`.
6. Staleness proof: agent blocks the API host for the Mac for four
   minutes (pf rule from the bridge, approved by the owner, removed
   after); menu row turns stale, status reads reconnecting, log shows the
   transition; after the rule is removed the row recovers without a
   relaunch.

Record under docs/deploys/ with pids, launchctl output, log lines, the
proof request id and the device id.

## Not in P18

Developer ID, notarization, Sparkle or any auto-update, quiet hours, free
text on the Mac, running on a second Mac, moving the device key to the
Secure Enclave, a Dock or window UI.

## Red gates (owner)

1. Ticking "Launch at login" (adds a background item to the owner's
   account; reversible from the same row or System Settings).
2. If macOS shows "requires approval": System Settings, General, Login
   Items and Extensions, section "Allow in the Background", row "HCA
   Notifier", switch on.
3. Day 2 Path B selection if SMAppService refuses the bundled agent.
4. Logout or restart of the owner's Mac for the login proof.
5. The temporary pf rule for the staleness proof (read-only on data,
   blocks one host for four minutes).

## Days

| Day | Build | Proof |
| --- | --- | --- |
| 1 | LoginItem.swift, Liveness.swift plus tests, menu rows, bundle.sh plist, version 0.2.0, docs | XCTest green on the Mac; bundle contains the plist; signature valid (`codesign --verify --deep`) |
| 2 | Owner ticks the row; proofs 2 to 6 above; record under docs/deploys/ | launchctl and log evidence for relaunch and login start; proof ask answered after login |

## Acceptance

1. After a forced kill the app is running again within 10 s without a
   click (launchctl pid change, log line).
2. After a logout and login the app is running within 15 s without a
   click and answers an ask from the banner.
3. A clean Quit stays quit.
4. The menu shows the last inbox read age and flags staleness within
   180 s of the last successful read; recovery needs no relaunch.
5. Nothing registers on a Mac where the owner never ticked the row.

## Status 2026-10-09 12:4x PT

Merged to main dd9bf029 (PR 67), Cloud Build 8579464c SUCCESS, revision
hca-api-service-00047-z2d at 100 percent. Design changed on Day 2 to Path B
(user LaunchAgent via launchctl) after the bundled SMAppService agent failed
to spawn without a Team ID; see
[the Day 2 record](../deploys/2026-10-09_p18_day2_path_b_proofs.md).

| Acceptance | State | Evidence |
| --- | --- | --- |
| 1 forced kill | proven | kill -9 71942 at 10:26:19, pid 72014 by 10:26:27 |
| 2 login start | deferred by owner 2026-10-09 12:4x PT | taken at the next natural logout or restart; agent reads launchctl and the log |
| 3 clean Quit | proven | exit 0, job state exited, no relaunch |
| 4 staleness 180 s | pending owner step | needs a network cut (Ethernet unplugged and Wi-Fi off) for more than 180 s |
| 5 nothing registers unticked | by construction | no plist is written until the row is ticked; Day 1 bundled registration removed |

