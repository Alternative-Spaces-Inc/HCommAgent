---
title: P20 Day 4 deploy and Day 5 restart proof
date: 2026-10-11
---

# P20 Day 4 deploy record (2026-10-11, 09:0x PT)

- PR 97 (p20/day4-two-process) merged by the owner, main 373fcf4f. Its Cloud Build d18a161a FAILED at pytest: the two process suite read an expire job row before the worker had written done_at (a test race, not a product defect). No revision was created.
- PR 98 (p20/day4-fix-race, test only) merged, main f16af4e1. Cloud Build 3c698080 SUCCESS.
- Deploy: revision hca-api-service-00072 serving 100 percent of traffic; service annotations maxScale 3, minScale 1 (read from `gcloud run services describe`).
- Live GET /v1/internal/metrics (admin bearer, 17:44 UTC), one instance answering: events newest_id 10, dispatcher cursor 10, lag 0, polls 2579; listener running and connected, notifications 2, errors 0; jobs worker running, polls 2587, claimed 1, done 1, failed 0, due_backlog 0, pending_total 2; replay replays 0, gaps 0, truncated 0; drops 0; db_pool size 2, checked out 1, overflow 0.

## Finding from the first metrics read: stream pile-up

The page listed 47 open streams on the instance, 46 of them from the same extension device (the unpacked Chrome load registered in P6) and all at last_sent_id 0. Cause, from extension/background.js: the poll alarm called connectStream() on every tick, which aborted the previous AbortController but, after a service worker suspend, that controller was gone while the proxy kept the old response open; each tick added a stream. Two fixes on the Day 5 PR:

- Server: HCA_STREAM_MAX_PER_DEVICE (default 4) open streams per device per instance. A fifth connect makes the oldest stream emit `event: stream.closed` with reason superseded and end; the metrics page reports streams.max_per_device and streams.superseded. Test: tests/test_inbox_api.py::test_fifth_stream_of_a_device_supersedes_the_oldest.
- Extension: the alarm reconnects only when no stream is being read (streamActive), and a stream.closed frame ends the read loop without reopening. Test: extension/tests/streamguard.test.js.
- Metric fix: a stream's position now starts at the dispatcher cursor when it connects, so an idle stream that connected after the newest row reads as caught up; the 46 idle streams showed as lag 10 (newest_id minus 0) when nothing was owed to them. Test: tests/test_metrics.py::test_stream_position_and_lag_follow_what_was_sent.

# P20 Day 5 restart proof

## Proof script

scripts/restart_proof.py (owner's agent or CI; needs HCA_ADMIN_TOKEN in the environment): mints an api key and a temporary P-256 device, opens the stream and receives one marker ask live (its id is the client's last seen id), closes the stream, creates three asks while nothing listens, optionally runs `--gap-cmd` (a command that kills and restarts the API and waits for /healthz), then reconnects twice, once with `?since=<id>` and once with the `Last-Event-ID` header, and asserts every missed ask arrives exactly once, ids ascending and above the marker, both reads identical. Cleanup cancels the asks and revokes the device and the key. Exit 0 only when every check passed; the last line is a JSON summary.

Local run on this PR (SQLite, uvicorn killed and restarted in the gap):

```
PASS stream connects and delivers a live frame: baseline id 9
PASS asks created while disconnected: 3 of 3
PASS gap command (API restart): exit 0
PASS every missed ask replayed (?since=): 3 of 3
PASS each exactly once (?since=): 3 frames
PASS ids above baseline, ascending: [10, 11, 12]
PASS same replay via Last-Event-ID header: [10, 11, 12]
PASS asks cancelled: 4 of 4
PASS device revoked: 200
PASS api key revoked: 200
{"restart_proof": "PASS", "asks_missed": 3, "checks": 12, "failed": []}
```

## In the pipeline

The smoke-local step of cloudbuild.yaml now runs the proof after the golden path on every build: uvicorn is killed and started again inside the gap, so every merge proves that events written before a restart reach a client that reconnects after it. The step's pass or fail is in each build's log.

First pipeline run (Cloud Build 4e3d5175 on main 98f77087, PR 102) FAILED at exactly one of the twelve checks, `gap command (API restart): exit 1`: the gap script waited for /healthz with curl, which the python:3.12-slim step image does not ship, so the wait timed out although the restarted API was up (the replay checks after it all passed). The step's first health wait had the same silent problem and spent its 30 s budget on every build. Fix on the follow-up PR: scripts/wait_healthz.py (urllib) replaces both curl loops, and restart_proof.py prints the gap command's last output lines when it fails.

## Live

A fresh device cannot read the live stream because HCA_REQUIRE_GOOGLE_LINK is 1 (P7), so the live proof is read from the linked clients and the metrics page rather than from a temporary device:

- API restart: the Day 4 rollover to revision 00072 restarted every instance while the owner's Mac Notifier and extension held streams; after the rollover the metrics page showed replay gaps 0, truncated 0 and drops 0, and the listener reconnected (connected true, errors 0). Replays 0 means no client had missed an event across the rollover (newest_id 10 was already delivered before it).
- Pending owner proof: an ask created while the Mac Notifier is stopped (`launchctl bootout` of the P18 agent, then `launchctl bootstrap`), expected to appear as one banner on restart with replay replays 1, replayed_events 1, gaps 0 on the metrics page. Recorded here when run.

## Status

Day 4 live on revision 00072 with max instances 3. Day 5 proof script, pipeline step and stream cap on this PR; the live owner proof is the last open item of P20.
