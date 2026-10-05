# HCA P14: surface routing, plan v1 (2026-10-05)

## Why this and why now

P13 Day 5 (docs/deploys/2026-10-05_p13_day5_surface_proofs.md) showed that
every linked device receives every ask at the same moment, and that two
surfaces on one machine (Chrome extension notification, HCA Notifier banner)
cannot be told apart by eye. In four takes the owner's tap reached the Chrome
copy each time. The ladder needs a routing rule, not a visual tweak: one
primary surface per owner, the others silent until the primary has not
answered within a window. This also makes the ladder real for the first time:
rung 1 and rung 3 stop being duplicates of each other.

## Facts checked before planning (repo main 056a0a5e)

- `GET /v1/inbox` returns every pending ask to every linked device
  (api/app/inbox_api.py, list_inbox). The stream publishes one envelope to
  every subscriber (api/app/events.py, publish) with no addressing.
- Both clients build their banner from the `request.created` event data and
  dedupe on `request.answered`, `request.cancelled`, `request.expired`
  (extension/background.js, macos/Sources/HCACore/InboxStore.swift).
- `Device` has label, surface, keys, google_sub, last_seen_at (set on each
  signed call in api/app/auth.py); no ordering field.
- The expiry sweep runs every 5 minutes from Cloud Scheduler
  (hca-expiry-sweep), too coarse for a fallback window.
- Three linked or active devices exist today: the Chrome device, the Mac
  device, and one revoked Chrome device.

## Shape

### Routing rule (server, the only place the rule lives)

- New column `devices.priority` (integer, default 100). Lower is earlier.
  Devices with equal priority share a rank and are shown together, so an
  unmodified deployment behaves exactly as today.
- Rank r of a device = number of distinct lower priority values among the
  owner's eligible devices (linked, not revoked, seen within
  `HCA_DEVICE_ACTIVE_SECONDS`, default 600). A device not seen recently
  drops out of the ranking so a closed laptop does not hold asks hostage.
- An ask is visible to a device of rank r from
  `created_at + r * window`, where window is
  `HCA_SURFACE_FALLBACK_SECONDS` (default 90) for ordinary asks and half
  of it for red gates. Rank 0 sees it at once.
- `GET /v1/inbox` applies the rule for the calling device. This is the
  fallback path and needs no timer: the 60 s poll in both clients picks up
  an ask the moment its window has passed.
- Stream envelopes gain `data.visible_to`: the list of device ids the event
  applies to right now. `request.created` names the rank 0 devices. A new
  event `request.escalated` (same item payload) is published when a later
  rank's window opens, from an in-process asyncio timer armed at ask
  creation; if the instance was recycled the poll covers it. Answered,
  cancelled and expired events stay unaddressed (every surface clears).
- Audit row unchanged. The answer record already names the surface.

### Admin route

- `PATCH /v1/devices/{device_id}` (admin bearer) with `{"priority": N}`.
  `GET /v1/devices` lists priority. Owner sets the order once from the
  runbook Terminal block behind an approval widget; no client UI in v1.

### Clients

- Extension and Mac app read their own device id (both already have it:
  `GET /v1/devices/self`) and ignore `request.created` or
  `request.escalated` envelopes whose `visible_to` does not name them.
  Envelopes without `visible_to` (older server) are treated as visible, so
  a client can ship before or after the server.
- Mac app re-posts a still pending ask whose banner was cleared every
  `window` seconds while pending (Day 5 finding 3).

### Not in P14

Time-sensitive entitlement and Developer ID signing (Focus breakthrough
stays a known limit), per-ask routing overrides, more than one owner, a
settings UI for priority.

## Days

| Day | Build | Proof |
| --- | --- | --- |
| 1 | Migration, priority column, routing module with unit tests (rank, window, red gate half window, inactive device skipped), inbox filter, stream `visible_to`, escalation timer, PATCH route | pytest; `GET /v1/inbox` from the rank 1 device is empty for a fresh ask and non-empty after the window |
| 2 | Extension and Mac app honour `visible_to`; Mac app periodic re-post; tests in both | Unit tests; a fresh ask shows on the primary only |
| 3 | Owner sets priorities (Terminal block); redo P13 proofs B (radio 4) and C (red gate) on the primary; fallback proof: leave an ask unanswered on the primary and see it arrive on the secondary after the window; record under docs/deploys/ | Records: B and C from the primary device; fallback record from the secondary with answered_at at least window after created_at |

## Owner decisions (red gates and selections)

1. Decided (Al, 2026-10-05 08:3x PT): HCA Notifier is primary (rank 0),
   the Chrome extension is rank 1. Reason: the Mac banner works with Chrome
   closed and the extension is the richer fallback for radio and text
   controls.
2. Fallback window: 90 s default (not changed by the owner).
3. Approve the migration deploy (merge to main triggers it) and the
   Terminal block that sets the priorities.

## Build mechanics

Branches p14/day1-routing, p14/day2-clients, p14/day3-proofs; one PR per
day, merged one at a time with its Cloud Build finished before the next.
Server tests under api/tests, extension tests under extension/tests, Swift
tests run on the owner's Mac through the bridge. Proof keys minted per proof
and revoked the same day (gcloud sign-in on the Mac is a precondition; the
three live proof keys from P6, P7 and P13 are revoked first).
