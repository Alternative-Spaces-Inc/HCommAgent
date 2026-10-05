# HCA P15: pending asks in the Mac menu, plan v1 (2026-10-05)

## Why this and why now

The charter's rung 4 is a dedicated menu bar app for collection responses
(docs/HCA_CHARTER_v1.md, Surfaces, item 4). After P13 and P14 the HCA
Notifier posts banners and holds settings, but the menu itself cannot show
or answer an ask. Two recorded gaps make that the next step: banners are
hidden under Focus and Sleep (P13 Day 5 finding 2) and a swiped banner is
seen again only after the 90 s re-post window (P14 proof C). The owner
selected this packet on 2026-10-05 16:2x PT over a stable signing identity
and per-device quiet hours.

## What ships

Client only, macos/ in this repo. No server or OpenAPI change: the menu
sends the same signed POST /v1/requests/{id}/answer as the banner, surface
macos_notification, path_taken [macos_notification, menu, <option_id>].

1. HCACore `MenuAskList`: a pure builder from the pending items to menu
   rows, ordered red gates first then by request id; each row carries the
   title line (RED GATE prefix when red_gate is set, truncated to 70
   characters), an optional why line, the options with their ids and labels,
   and the suggested option id. Unit tested (order, prefix, truncation,
   suggested marker, empty list row).
2. HCANotifier menu: the "Pending: N" line becomes "Pending asks (N)" and is
   followed by one row per ask. Each row opens a submenu: the why line
   (disabled), a separator, then one line per option, "label (suggested)"
   marked with a checkmark on the suggested one. Clicking an option sends the
   answer, writes "Last action: menu <option> on <id>", withdraws the banner
   and drops the row. With no pending asks the section shows one disabled
   line "No pending asks". Rows are rebuilt on every inbox delta.
3. Submenu items follow the PR 54 rule: explicit target, enabled, parent
   submenu autoenablesItems false.

## Not in P15

Free-text fields (control other than approve_deny and radio stays
banner-only with an "open in Chrome" row), per-ask routing overrides, a
window or popover UI, quiet hours, signing identity.

## Proof

Post an ask under a short-lived agent key while the owner's Mac is in a
Focus mode that hides banners (or the banner is swiped at once). The owner
opens the menu, finds the row, picks an option from its submenu. The answer
record read with the agent key shows surface macos_notification and
path_taken [macos_notification, menu, <option_id>]; the server log shows
the POST; the key is revoked after the read. Record under docs/deploys/.
