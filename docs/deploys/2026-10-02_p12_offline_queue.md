# 2026-10-02: HCA-P12, offline answer queue (plan Day 8)

## Why

The plan's Day 8 acceptance test: disconnect the network, answer an ask in
the popup, reconnect, and the queued answer reaches the API by itself. Until
now a popup answer with no network showed "Failed to fetch" and the owner
had to come back and click again.

## Design

- `lib/offlinequeue.js`: the queue lives in `chrome.storage.local` under
  `hca_answer_queue`, one entry per request id (a later answer for the same
  ask replaces the earlier one). It holds the unsigned answer body, not a
  signature: device signatures carry a timestamp and a single-use nonce the
  server checks (P6), so anything signed offline would be stale on replay.
  Signing happens at send time in the service worker through the same
  `postAnswer` path the popup uses.
- Popup: only a transport failure (fetch threw, no response) queues. Any
  server reply, 5xx included, is shown as an error: we were online, and
  retrying a human decision blindly could double-post it. A queued card
  leaves the list and a yellow notice counts saved answers.
- Service worker: flushes before every poll (1 minute alarm), on every
  stream connect or reconnect, and immediately when the popup asks via
  `hca_flush_queue`. Outcomes: 2xx sent (drop); 409, 404, 410 stale (drop,
  the ask was answered elsewhere, cancelled or expired, and the audit row
  exists); transport failure or 5xx retry (keep, stop the flush, still
  offline); other 4xx kept up to 20 attempts (a device the owner is about
  to re-link still delivers), then dropped with a desktop notification.
- Server untouched. The 409 rule from P6 is what makes replay idempotent.

Tests: extension 35 (ten new: enqueue, classification, apply, flush with a
fake storage incl. the 409 and the stop-on-offline cases).

## Owner proof steps (full path each, run one at a time)

1. HCA posts a proof ask (radio A/B) and confirms it reached the popup.
2. Take the Mac fully offline. Check the default route first from the
   bridge (`route -n get default`): this Mac routes through a USB Ethernet
   adapter, so Wi-Fi off alone leaves it online. Unplug that adapter's
   network cable, then Mac menu bar, Wi-Fi icon (top right), Wi-Fi switch
   off.
3. Chrome toolbar, HCA icon, popup: on the proof card choose B and click
   Submit. The card disappears and the yellow notice reads "1 answer saved
   offline".
4. Mac menu bar, Wi-Fi icon, toggle Wi-Fi back on.
5. Within about a minute HCA reads the request record from the server:
   answered, value b, device dev_ddea0aae, and the popup notice is gone.

## Proof

Merged to main `91e14a6b` (PR 36) 2026-10-03 and loaded unpacked; the
reloaded worker polled `/v1/inbox` every minute with 200s (server logs,
14:21Z on), so the P12 code runs clean. The "Errors" entries on the
extensions card are the pre-P11 iconUrl entries Chrome keeps until Clear
all; no new error since the icons fix.

Take 1 (request `03ce7cd3`, 2026-10-03 14:56Z): not a queue test. The
owner turned Wi-Fi off and answered B; the server received the answer
directly (`POST .../answer` 201 at 14:56:22Z) and the one-minute inbox
polls never paused. Cause: the Mac's default route is the USB Ethernet
adapter (`en13`, "USB 10/100/1G/2.5G LAN"), so Wi-Fi off did not take it
offline. The popup behaved correctly for an online answer (card removed,
no offline notice). Step 2 above now says so.

Take 2 (request `993bfdc2`) was posted and then cancelled when the owner
stopped for the day. Proof key `63a9eeb4758d` (caller agent-p12-proof)
revoked 15:07Z.

Take 3 (request `9ec2d7ed`, key `204d38b5fb92`, 2026-10-03): the queue
proven. The owner unplugged the Ethernet cable and turned Wi-Fi off,
answered B in the popup (card removed, yellow "1 answer saved offline"
notice shown), then reconnected.

| time (UTC) | server log |
| --- | --- |
| 15:20:24 | GET /v1/inbox 200 (last poll before offline) |
| 15:21:24 | no poll: offline |
| 15:22:24 | POST /v1/requests/9ec2d7ed/answer 201, then GET /v1/inbox 200 |
| 15:22:24 | POST /v1/requests/9ec2d7ed/answer 500 (see defects) |
| 15:23:24 | GET /v1/inbox 200, queue empty, no replay |

Record: state answered, value b, answered_by Al MacBook Chrome, device
dev_ddea0aae221d2e3b9cedd9e4c05e260e, surface browser_overlay, answered_at
15:22:24Z, that is one minute after the answer was given offline. The
owner was asked nothing while offline: the three steps were handed over
in one message before disconnecting.

Two defects found by the proof, both fixed in the follow-up PR:

1. Extension: `flushOfflineAnswers` took its `flushing` guard after an
   await on storage, so the poll alarm and the stream reconnect, firing in
   the same tick when the network returned, both posted the answer. Guard
   now taken synchronously.
2. Server: the loser of that race hit the unique constraint
   `audit_rows_ask_id_key` on commit and got a 500 (IntegrityError
   unhandled). It now gets the same 409 "request already answered" a late
   second answer gets, so the client drops it as stale. Data was never at
   risk: the constraint kept one decision per ask.
