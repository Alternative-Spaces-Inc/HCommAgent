# HCA-P14 surface routing: Days 1 to 3, proofs A and B

Date: 2026-10-05 (08:3x to 10:5x PT). Plan: docs/plans/HCA_P14_SURFACE_ROUTING_v1.md.

## What shipped

| Day | PR | main | Cloud Build | Content |
|---|---|---|---|---|
| 1 server | 49 | 82624b5e | dd8acfba SUCCESS 15:57:04Z | app/routing.py, migration a1b2c3d4e5f6 (devices.priority default 100), inbox filter, visible_to on request.created, request.escalated timers, PATCH /v1/devices/{id} priority, OpenAPI. 83 API tests (8 new). |
| 2 clients | 50 | a99c6255 | 2e9901cf SUCCESS | Extension isAddressedTo and request.escalated handling (37 tests, 2 new). Mac app StreamData.visible_to, InboxStore.apply(event:deviceId:), repostDue plus repostCleared after every poll, 90 s (24 Swift tests on the owner Mac, 3 new). |
| 3 ladder | this record | | | Priorities set by admin PATCH: Al MacBook Notifier 10, Al MacBook Chrome 200 (both 200 OK). ~/hca-extension refreshed from a99c6255 and reloaded; HCA Notifier rebuilt (CFBundleVersion a99c625) and relaunched. |

## Proofs

Proof key agent-p14-proof minted 17:07Z, revoked 17:4xZ with agent-p13-day5,
agent-p6-proof and agent-p7-proof (four revokes, all 200). Live keys left:
two smoke-<ts> keys from earlier raced builds, to revoke at the next admin
session.

A. Plain radio, request e8b6a544, posted 17:07:46Z. The Mac banner showed it
at once; Chrome's inbox polls returned 200 every 20 to 60 s throughout with
the ask filtered out for the first 90 s. Answered from the Mac banner:
value mac, surface macos_notification, device dev_41d7938b, path
[macos_notification, banner, option_0], 17:38:00Z. Dedupe held across the
30 minutes the ask stayed open on both surfaces.

B. Red gate, request e585a82a, posted 17:40:02Z, half window (45 s). The Mac
banner was left alone on purpose. Chrome received the ask after the wait and
the answer was given in the Chrome popup: value approve, surface
browser_overlay, device dev_ddea0aae, 17:41:07Z (65 s after creation, 20 s
after the window opened). Notification Center on the Mac held zero banners
afterwards: the Mac app withdrew its banner on request.answered.

C. Cleared banner re-post, request 2c0e59ce, posted 17:47:46Z under a
short-lived key agent-p14-proofc (revoked after the read). The owner swiped
the Mac banner away without answering; the app re-posted it (repostCleared
runs after each 60 s poll, once the 90 s window since the last post has
passed) and the owner answered the re-posted banner: value came_back,
surface macos_notification, path [macos_notification, banner, option_0],
18:27:34Z. The ask had been re-posted repeatedly across the 40 minutes it
stayed open, so a cleared ask no longer disappears until restart.

Result: the primary-then-fallback ladder works end to end on live, with the
rule on the server and both clients honouring visible_to, and a cleared Mac
banner comes back while the ask is pending.

## Findings

1. P13 Day 5 findings 1 and 2 (surfaces indistinguishable, every device gets
   every ask) are closed by routing: only one surface shows an ask at a time
   until its window lapses.
2. Finding 3 (cleared banners never re-post) is closed by proof C.
3. The Day 2 Cloud Build (a99c6255) appeared in the build list about 20
   minutes after the merge; the Day 1 build appeared at once. Check before
   assuming a trigger did not fire.
4. The agent sandbox was reset mid Day 2 with uncommitted client edits; they
   were re-applied from the scripted edit and pushed within minutes. Commit
   and push after every file-level step, not after a day's work.
5. Server log rows for /v1/inbox do not carry the device id, so per-device
   inbox evidence has to come from client state or the answer record.

6. GET /v1/requests/{id} returns the answer under the key answer; the
   device id is not part of that view (surface and path_taken are). The
   signed audit route carries the device id.

## Next

Owner-side priority controls (device priority in the Mac menu and the
extension options page) so the owner can change rank without the admin
token; then the P14 closing record. Two smoke-<ts> keys still live.
