# HCA-P14 surface routing: priority controls and closing record

Date: 2026-10-05 (12:0x to 14:1x PT). Plan: docs/plans/HCA_P14_SURFACE_ROUTING_v1.md.
Earlier records: 2026-10-05_p14_surface_routing_proofs.md (Days 1 to 3, proofs
A, B) and PR 52 (proof C).

## What shipped

| PR | main | Cloud Build | Content |
|---|---|---|---|
| 53 | bb9a929a | 3ef19696 SUCCESS | Server: PATCH /v1/devices/self (device signature, same linked-device gate as answering, declared before the admin PATCH /{device_id} so the literal path wins, shared 0 to 1000 range check), OpenAPI entry. Extension: options page section Surface priority (Primary 10, Default 100, Fallback 200), reads GET /v1/devices/self, saves with a signed PATCH; lib/api.js setSelfPriority, PRIORITY_CHOICES, priorityLabel. Mac app: menu line Priority plus submenu Set this Mac's surface priority with a checkmark on the current place; APIClient.setPriority; HCACore SurfacePriority. Tests: API 84 (1 new), extension 38 (1 new), Swift 25 on the owner Mac (1 new). |
| 54 | pending at the time of writing | | Mac app: submenu items carry target = self, isEnabled = true, submenu autoenablesItems = false; choosePriority writes "Last action: priority click (n)" before any guard. See defect 1. |

One gate fix inside PR 53: an unquoted comma in an OpenAPI flow-mapping
description ("Device unknown, revoked or not Google-linked") made YAML read a
second key; quoted (4b743d2), gate green.

## Proofs

Both from the owner, no admin token involved, verified by an admin read of
GET /v1/devices and the Cloud Run request log afterwards.

Take 1 (bundle bb9a929). The owner clicked Fallback in the Mac submenu. The
menu stayed "Priority: Primary (10)", the server received no PATCH (log
checked at 19:37Z, 10 minute window), devices read Notifier 10. Defect 1.

Take 2 (bundle f97d3b7, branch of PR 54 built on the Mac). Menu showed
"Priority: Fallback (200)" and "Last action: priority set to 200"; server log
PATCH /v1/devices/self 200 at 20:43:51Z; admin read: Al MacBook Notifier 200,
Al MacBook Chrome 200.

Options page (Chrome, ~/hca-extension refreshed from bb9a929a and reloaded).
The Surface priority section rendered and read the Chrome device from the
server: "Current: Fallback (200)".

Restore. The owner clicked Primary in the Mac submenu; menu "Priority:
Primary (10)"; admin read at 20:54:52Z: Al MacBook Notifier 10, Al MacBook
Chrome 200. Ladder as designed.

## Defects and findings

1. Submenu click was a no-op. Top-level status menu items with a nil target
   reach the app delegate through the responder chain; the items inside the
   submenu did not on the owner's Mac (no PATCH, no menu change). Fix in PR
   54: explicit target, always enabled, and a visible "Last action" line on
   click so a silent no-op cannot hide again.
2. Keychain prompt hides the app. A rebuilt ad-hoc signed binary has a new
   code signature, so the first Keychain access to the device key prompts the
   owner to authenticate. Until he does, the process is alive but shows no
   menu bar icon (System Events reported no menu bar 2 for the process; no
   crash report). The owner found and answered the prompt; the icon returned.
   Mitigation for later rebuilds: tell the owner to expect the prompt, or
   sign with a stable identity once one exists.
3. GitHub Actions major outage during PR 54: two gate jobs sat queued with no
   runner (Extension Tests, OpenAPI validation) after a first run was
   cancelled at 15 minutes. Rule kept: merge only on a full green set.
4. Wrong bundle script path in the refresh step (scripts/mac_bundle.sh instead
   of macos/scripts/bundle.sh) relaunched the old a99c625 bundle once; caught
   by reading CFBundleVersion before the proof. Always read the version after
   a rebuild.

## P14 closing

All plan items are proven: server routing (Day 1), client handling (Day 2),
ladder set and proofs A, B (Day 3), proof C re-post (PR 52), owner-side
priority controls on both surfaces (this record). Live keys: two smoke-<ts>
keys from earlier raced builds remain, to revoke at the next admin session.

## Next phase candidates

Chrome Web Store packaging to retire unpacked loads; a stable signing
identity for the Mac app (removes defect 2 and unlocks time-sensitive
banners); per-device quiet hours on the ladder.
