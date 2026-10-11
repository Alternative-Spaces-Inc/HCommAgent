---
title: P20 Day 3 deploy, leased DB jobs for escalation and expiry
date: 2026-10-11
---

# P20 Day 3 deploy record (2026-10-11, 08:0x PT)

- PR 95 (p20/day3-leased-jobs) merged by the owner, main e56c06af. PR 94 (Day 2 record) was folded into it and closed with a note.
- Cloud Build c9271754 (region us-west1) SUCCESS.
- Migrate job execution hca-migrate-vbjtl: `Running upgrade e5f6a7b8c9d0 -> f6a7b8c9d0e1` then `OK: database at head f6a7b8c9d0e1 with all 13 model tables present` (read from the job's own log).
- Deploy: revision hca-api-service-00071 serving 100 percent of traffic.
- smoke-live: all PASS, `OK agent path`.
- Live GET /health after deploy: `{"status":"ok","db":"ok","events":{"listener":"connected","notifications":2,"errors":0},"jobs":{"worker":"running","polls":103,"claimed":1,"done":1,"failed":0}}`. The worker thread had already claimed and closed one job (the smoke test's ask) within its first minutes.
- CI on the PR: 13 required checks green; the Postgres job ran tests/test_jobs.py with FOR UPDATE SKIP LOCKED in the claim path (140 passed).

## What is live now

- Every ask writes its timed actions as scheduled_jobs rows in the create transaction: one escalate per later rank at the instant its window opens, one expire at expires_at. The rows survive a restart and a redeploy.
- A worker thread per instance polls every 2 s (HCA_JOBS_POLL_SECONDS), leases due rows with a compare-and-set on lease_expires_at (FOR UPDATE SKIP LOCKED on Postgres), runs them (routing.fire_escalation, expiry.expire_ask, both idempotent against the ask state), records result and done_at; a failing job is released with exponential backoff and closed after five attempts with last_error. A lease a dead instance held becomes claimable after 60 s.
- reconcile runs every 60 s and on each sweep hit: an overdue non-terminal ask without an open expire job gets one due now, a future-dated one is pulled forward. Asks created before this table are covered.
- The Cloud Scheduler sweep (POST /v1/internal/expiry-sweep, every 5 min) remains as the second trigger of the same leased path; response shape unchanged plus jobs_run and reconciled.
- The P14 in-process asyncio timers are removed.

## Not yet live

- Two process suite, lag and drop metrics, _MAX_INSTANCES 3 (Day 4), restart proof (Day 5).
