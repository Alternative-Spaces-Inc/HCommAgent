# HCA P15: pending asks answered from the Mac menu, proof record (2026-10-05)

Plan: docs/plans/HCA_P15_MENU_ASKS_v1.md. Branch p15/menu-asks, PR 56.
Builds on the owner Mac: 6ee216a (rows), ead12b4 (rows plus action log).
Swift tests: 29 pass (4 new in MenuAskListTests).

## Proof

Take 1, request c4b58d78, key afe344e16f8e (revoked): not a menu proof.
The owner reported a menu click, but the server log holds exactly one
answer POST (23:38:53Z, 201) and the record has surface browser_overlay
with no path_taken, which is the Chrome extension's body shape. The click
had landed on the Chrome notification that arrived after the 90 s window.
Same lesson as P13 Day 5: the surfaces are not told apart by eye, so the
record and the log decide, never the report.

Fix before take 2 (ead12b4): every owner action and its outcome is written
to the unified log (subsystem com.alternativespaces.hca.notifier, category
actions) as well as the "Last action" menu line, so a proof is read with
`log show` instead of asking the owner to read the menu (Rule 0).

Take 2, request ac5d096c, key c58b9aa5234c (revoked after the read), posted
23:42:59Z. App log:

    16:45:21.284 menu click from_menu on ac5d096c...
    16:45:21.284 sending menu from_menu for ac5d096c...
    16:45:21.390 answered from_menu on ac5d096c...

Record read with the proof key: state answered, value from_menu, surface
macos_notification, path_taken [macos_notification, menu, from_menu],
device dev_41d7938b (Al MacBook Notifier), answered_at 23:45:21Z,
duration_ms 98437 (from first banner post to the menu click). After the
revoke the key reads 403. Live keys: the two smoke keys only.

## Correction to the P14 closing record

docs/deploys/2026-10-05_p14_priority_controls_and_closing.md says a rebuilt
ad-hoc binary shows no menu bar icon until the owner authenticates a
Keychain prompt. That is wrong. A menu bar capture (screencapture over the
bridge, cropped and viewed by the agent) shows the HCA icon present the
whole time, about 1370 px left of the clock, leftmost in a row of some
thirty status icons on the owner's 4K display. No SecurityAgent process was
running and the app had already sent signed requests. The icon was hard to
find, not missing. Remedy: the owner can Command-drag the icon next to the
clock; the position persists across launches.

## Open

Free-text controls show "Needs typed input: answer in Chrome" in the
submenu (not answerable from the menu by design). The menu is rebuilt on
every inbox delta; a menu left open during a delta closes and reopens on
the next click.
