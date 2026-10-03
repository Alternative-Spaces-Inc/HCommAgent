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
revoked 15:07Z. Open: repeat with the Ethernet cable unplugged and Wi-Fi
off, then read the record and the poll gap in the server logs.
