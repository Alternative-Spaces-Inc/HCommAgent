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

Second pipeline run (Cloud Build cf3315c5 on main 46339e25, PR 105) FAILED at the first check, `stream connects and delivers a live frame: baseline id None`: the marker ask's frame reached the stream before the posting thread had parsed its 201 and recorded the request id, so the reader compared the frame against an empty marker and dropped it, then waited 20 s for nothing. The fast health wait made this race likely (the API was ready in 2.1 s instead of after a 30 s timeout). Fix: the reader keeps every request.created frame it sees and matches once the marker id is known. Five consecutive local runs with API restarts: 12 of 12 PASS each.

## Deploy (12:1x PT)

- PR 106 merged by the owner, main cdda65de. Cloud Build cba52eac SUCCESS: smoke-local ran the golden path and the restart proof (`{"restart_proof": "PASS", "asks_missed": 3, "checks": 12, "failed": []}`, baseline id 3, uvicorn killed and restarted in the gap), then pack, build, push, migrate (no new revision, head f6a7b8c9d0e1), deploy, smoke-live PASS (`OK agent path`).
- Revision hca-api-service-00073 serving 100 percent.
- Live GET /v1/internal/metrics on 00073 (19:22 UTC): events newest_id 12, dispatcher cursor 12, lag 0; streams open 4 from the owner's extension device, all at last_sent_id 12, max_per_device 4, superseded 0; drops 0; replay replays 0, gaps 0, truncated 0; listener connected, errors 0; jobs worker running, due_backlog 0, pending_total 3; db_pool size 2, checked out 1. The 46 stream pile-up seen on 00072 is gone: the cap holds the device at four. The extension's own reconnect guard takes effect when the unpacked load is refreshed from main.

## Live

A fresh device cannot read the live stream because HCA_REQUIRE_GOOGLE_LINK is 1 (P7), so the live proof is read from the linked clients and the metrics page rather than from a temporary device:

- API restart: the Day 4 rollover to revision 00072 restarted every instance while the owner's Mac Notifier and extension held streams; after the rollover the metrics page showed replay gaps 0, truncated 0 and drops 0, and the listener reconnected (connected true, errors 0). Replays 0 means no client had missed an event across the rollover (newest_id 10 was already delivered before it).
- Pending owner proof: an ask created while the Mac Notifier is stopped (`launchctl bootout` of the P18 agent, then `launchctl bootstrap`), expected to appear as one banner on restart with replay replays 1, replayed_events 1, gaps 0 on the metrics page. Recorded here when run.

## Live owner proof (12:3x PT, revision 00073)

Run from the owner's Mac by the agent over the bridge: `launchctl bootout` of the P18 login agent (no Notifier process after 3 s), one ask created with a temporary api key while nothing listened (request 2d91eb3a, event id 13, title "P20 restart proof: did this banner appear once?", radio Once / Twice or more / Never), `launchctl bootstrap` 5 s later (Notifier pid 76764 running under launchd), key revoked.

- Metrics 20 s after the restart: the Mac device's stream open at last_sent_id 13, drops 0, gaps 0.
- The owner answered on the banner: state answered, value `twice`, answered_by "Al MacBook Notifier", surface macos_notification, path [macos_notification, banner, option_1], duration 306 s. Audit row carries the Mac device id.
- Delivery after a process restart is proven: an ask created while the Notifier was down reached the owner and was answered through HCA itself.

Two findings:

1. The owner saw the banner more than once over the 306 s. The store dedupes the inbox read against the stream (`shown` set), so a startup double post is excluded by code; the P14 repost of a cleared banner every 90 s is the expected cause, but the Notifier writes no log line per post, so this run cannot distinguish the two. Follow-up: a per-post log line (request id, reason: added, repost) in the Notifier.
2. Metrics replay counters stayed at 0: the Mac app keeps its Last-Event-ID in memory only, so a fresh process reconnects without one and the ask arrived through the startup inbox read, not through SSE replay. The server side replay is proven by the pipeline proof; the Mac client does not yet use it across a restart. Follow-up: persist the last event id (UserDefaults) and send it on the first connect.

## Mac client follow-up (14:1x PT, Mac build ede2bf3, version 0.2.1)

Repeating the proof on the rebuilt Notifier exposed the root cause behind both findings: the Mac app read the stream with `URLSession.AsyncBytes.lines`, and AsyncLineSequence drops empty lines (probed on the owner's Mac: `"id: 1\nevent: x\ndata: {}\n\nid: 2\n\n"` yields four lines, no empty one). The empty line is the SSE frame boundary, so the parser never dispatched an event: since P13 Day 3 every ask reached the Mac through the 60 s inbox poll, Last-Event-ID never advanced, and the first proof's 306 s and replays 0 follow from that. The repost finding is explained the same way: with no live `request.answered` event the banner was cleared only on the next poll.

Fix (PR 108): the app reads raw bytes and feeds the parser per newline, the last event id persists in UserDefaults (`EventCursor`, sent on the first connect after a relaunch), one log line per post with its reason (`posts` category). Four parser tests pin the live frame shape, including the no-empty-lines case that must yield nothing. 47 Swift tests pass on the Mac.

Proof on the fixed build, same procedure (bootout, ask while down, bootstrap):

| Check | Result |
| --- | --- |
| Primer ask posted live | `post 8a9984c3 added` 0.9 s after creation, cursor stored 29 |
| First connect after relaunch | `stream open lastEventId=29` |
| Server replay | metrics replays 1, replayed_events 1, gaps 0, truncated 0, drops 0 |
| Ask created while down | `post ff3c70b8 added` once, 0.1 s after the stream opened |

Owner answer on the ff3c70b8 banner (expected Once) is recorded in the next record.

## Status

Day 4 live on revision 00072 with max instances 3. Day 5 live on revision 00073: proof script passing in the pipeline on every build, stream cap live, live owner proof done (answered on the banner after a Notifier restart). Root cause (AsyncLineSequence drops the SSE frame boundary) fixed in PR 108, replay 1 of 1 proven on the rebuilt Mac app.
