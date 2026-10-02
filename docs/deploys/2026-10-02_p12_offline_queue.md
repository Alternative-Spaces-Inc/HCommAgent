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
2. Mac menu bar, Wi-Fi icon (top right), click it, then click the Wi-Fi
   toggle at the top of the menu to turn it off.
3. Chrome toolbar, HCA icon, popup: on the proof card choose B and click
   Submit. The card disappears and the yellow notice reads "1 answer saved
   offline".
4. Mac menu bar, Wi-Fi icon, toggle Wi-Fi back on.
5. Within about a minute HCA reads the request record from the server:
   answered, value b, device dev_ddea0aae, and the popup notice is gone.

## Proof

Filled in after the owner steps.
