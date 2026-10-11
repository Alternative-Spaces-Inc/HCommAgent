# HCA P20: durable delivery, plan v1 (2026-10-09)

## 0 Summary

P20 turns the P20 row of docs/plans/HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md into a day-by-day build plan that replaces the single-instance in-memory event bus and background task mechanics with durable database-backed storage, event streaming, and leased worker loops for agentic retail, marketing, merchandising and creative operations workflows. Today, event delivery relies on in-process asyncio queues, request nonce replay guards are kept in process memory, escalation timers run on a single instance event loop, and deployment forces `_MAX_INSTANCES: "1"`. P20 establishes a durable `events` table with `Last-Event-ID` SSE replay, Postgres `LISTEN/NOTIFY` fan-out with configurable polling fallback, a persistent `nonces` table with TTL cleanup, leased database jobs for escalation and expiry, per-stream authorization rechecks on every tick, and operational metrics for lag, drops, and replay gaps, enabling Cloud Run horizontal scaling to `_MAX_INSTANCES: "3"`.

## 1 Current behaviour and its failure modes

Verified against repo files on branch origin/p19/day1-tenancy (head 37c0282):

- In-process event bus and lost envelopes on restart:
  `api/app/events.py` (lines 28 to 38 and 41 to 77) maintains subscriber state in module-level `_subscribers` mapping `asyncio.Queue` objects to `Subscriber` dataclass instances. `publish()` (lines 57 to 77) puts event envelopes directly into memory queues (`sub.queue.put_nowait(envelope)` at line 74). If the API container process restarts or recycles, all pending in-memory envelopes vanish.
- Single instance constraint:
  `cloudbuild.yaml` (line 38) hardcodes substitution `_MAX_INSTANCES: "1"` with the comment `# in-process event bus; see api/Dockerfile`, enforced during `gcloud run deploy` (line 167). Deploying multiple instances splits subscribers across containers without event replication.
- In-memory nonce replay guard:
  `api/app/devicesig.py` (lines 91 to 120) implements `ReplayGuard` using a process-local dictionary `self._seen: dict[tuple[str, str], float]` (line 96) protected by `threading.Lock()` (line 97). `check_and_record()` (lines 99 to 113) checks pairs against in-memory history. Lines 25 to 27 explicitly state that single-instance deployment (`_MAX_INSTANCES: "1"`) is what makes in-memory replay prevention sufficient today. Across multiple processes, a replayed signed request sent to a different instance bypasses the check.
- In-process escalation timers:
  `api/app/routing.py` (lines 114 to 163) manages escalation timers on the event loop via `_loop.create_task()` (lines 130 to 132 and 158) bound at startup in `api/app/main.py` (lines 31 to 37). If a process recycles while `asyncio.sleep()` (lines 149 to 155) is waiting, the scheduled `request.escalated` event is lost, relying solely on client polling fallback (lines 9 to 15).
- In-process expiry sweep invocation:
  `api/app/expiry.py` (lines 82 to 140) and `api/app/sweep_api.py` (lines 40 to 43) execute auto-deny expiry logic synchronously via HTTP POST `/v1/internal/expiry-sweep`. There is no database lock or job leasing, creating race conditions if sweeps run concurrently or miss execution when scheduler calls fail.
- Per-stream authorization check during keep-alive:
  `api/app/inbox_api.py` (lines 48 to 77) rechecks device authorization (`_device_still_authorized` at lines 48 to 53) only upon keep-alive timeouts (lines 70 to 73). Streamed events are delivered from queue without checking authorization status on every event yield.

## 2 Target design

- Events table schema:
  Alembic migration creates `events` table with columns: `id` (bigserial primary key, strictly increasing), `type` (varchar, e.g. `request.created`, `request.escalated`, `request.answered`, `request.cancelled`, `request.expired`), `request_id` (varchar, nullable, indexed), `tenant_id` (varchar, foreign key to `tenants.id`, indexed), `schema_version` (integer, default 1), `visible_device_ids` (JSON, array of allowed device IDs or null), `payload` (JSON, full event data), and `created_at` (datetime with timezone, UTC default).
- SSE reads with Last-Event-ID replay:
  `GET /v1/inbox/stream` accepts an optional `Last-Event-ID` header or query parameter. On stream connect, the server queries `events` where `tenant_id = device.tenant_id` and `id > last_event_id`, filters rows against `visible_device_ids` and visibility rules, and streams historical events before yielding real-time events. Each SSE message includes `id: <event_id>`.
- Postgres LISTEN/NOTIFY fan-out with poll fallback:
  On writing an event, `NOTIFY inbox_events, '<tenant_id>:<event_id>'` is executed. API instances run a background listener loop on an async connection. Upon receiving a notification, instances signal active SSE stream tasks for that tenant. A fallback polling loop queries `events` every N seconds (default 2.0s) for missed IDs, guaranteeing event delivery despite driver or network drops.
- Nonces table with TTL cleanup:
  `nonces` table created with columns `device_id` (varchar), `nonce` (varchar), `created_at` (datetime), and composite primary key `(device_id, nonce)`. `api/app/devicesig.py` verifies nonces via atomic database insertion (`INSERT INTO nonces`). A background worker deletes records where `created_at < now - 300s`.
- Escalation and expiry as leased DB jobs:
  Asks or `scheduled_jobs` rows carry `lease_owner` (varchar), `lease_expires_at` (datetime), and `next_run_at` (datetime). A background worker loop in each API instance uses `SELECT ... FOR UPDATE SKIP LOCKED` to acquire overdue items, execute escalation or expiry state transitions, publish events to `events`, and update job state.
- Per-stream authorization recheck:
  `inbox_event_source` checks `_device_still_authorized(device_id)` and tenant status at each event tick before streaming data to clients.
- Metrics:
  Expose metrics for stream lag (max `events.id` minus client delivered `id`), queue drops, and replay gaps.
- Scaling:
  Set `_MAX_INSTANCES: "3"` in `cloudbuild.yaml`.

## 3 Migration and compatibility

- Client contract preserved:
  Chrome extension (`extension/background.js`) and macOS app (`macos/Sources/HCACore/InboxStore.swift`) keep existing event names (`request.created`, `request.escalated`, `request.answered`, `request.cancelled`, `request.expired`).
- Optional Last-Event-ID header:
  `Last-Event-ID` header is optional. Legacy clients without `Last-Event-ID` receive real-time stream events starting from connect time without breaking changes.
- In-process bus shim:
  `api/app/events.py` remains as a compatibility wrapper that writes to `events` table and triggers notifications, maintaining backward compatibility for existing test suites.

## 4 Days

| Day | Scope | Tasks | Deliverables and Proofs |
| --- | --- | --- | --- |
| Day 1 | Events table, writer, and SSE reader with replay | Alembic migration for `events` table; update `publish()` to write rows; update `inbox_event_source()` to query `events` with `Last-Event-ID` replay. | Pytest suite verifying event DB persistence and historical SSE event replay upon reconnection with `Last-Event-ID`. |
| Day 2 | LISTEN/NOTIFY, poll fallback, nonces table | Implement Postgres `LISTEN/NOTIFY` listener and polling fallback; migration for `nonces` table; update `ReplayGuard` to use database nonces. | Integration tests confirming cross-process event notifications and persistent nonce replay rejection across instance restarts. |
| Day 3 | Leased DB jobs for escalation and expiry | Replace in-process asyncio escalation timers and sweep endpoint with leased DB worker loop using `SELECT FOR UPDATE SKIP LOCKED`. | Concurrent worker test proving single execution leasing for escalation and expiry across competing workers. |
| Day 4 | Multi-process integration test and metrics | Configure two-process test environment; add stream lag, drop, and replay gap metrics; update `cloudbuild.yaml` `_MAX_INSTANCES` to `3`. | Two API processes, two subscribers, two workers integration test suite passing in CI; metrics endpoint verified. |
| Day 5 | Deploy, verification record, documentation | Deploy P20 to Cloud Run; run restart proof test publishing events while API is down; record proof in `docs/deploys/`. | Verification record in `docs/deploys/` showing events published during API downtime delivered on client reconnect. |

## 5 Tests and proofs

- Restart delivery test:
  1. Stop or pause the API server instance.
  2. Publish an ask/event directly to the database.
  3. Start the API instance and connect an SSE client with `Last-Event-ID`.
  4. Confirm the event published during downtime is delivered immediately upon connection.
- Multi-process integration test:
  1. Start two distinct API processes connected to the same database.
  2. Connect subscriber A to Process 1 and subscriber B to Process 2.
  3. Run background workers on both processes.
  4. Publish events and trigger escalation/expiry transitions.
  5. Verify both subscribers receive all relevant tenant events without duplicates, and leased jobs execute exactly once.

## 6 Owner decisions

All four taken 2026-10-10 18:2x PT, recorded as decision 6 in docs/decisions/DECISIONS_20261010.md: 1 LISTEN/NOTIFY plus 2 s poll, 2 24 h or 1,000 events, 3 _MAX_INSTANCES 3, 4 dual mode.

### Selection 1: Event Fan-Out Mechanism
- Title: LISTEN/NOTIFY versus Poll-Only Fan-Out
- Suggested option: Postgres LISTEN/NOTIFY with 2-second polling fallback.
- Why: Provides sub-10ms delivery latency in normal operation while guaranteed polling fallback ensures 100% reliability if database connections drop.
- Option A (Suggested): LISTEN/NOTIFY with 2s poll fallback. Sub-10ms latency, resilient against connection drops.
- Option B: Poll-only fan-out every 1 second. Simple design, but introduces 1s average latency and higher database query load under idle conditions.
- Option C: External pub/sub message broker (e.g. Redis or Cloud PubSub). Adds operational complexity and extra hosting cost when Cloud SQL Postgres is already available.

### Selection 2: SSE Replay Window Length
- Title: Replay Window Length for Disconnected Clients
- Suggested option: 24 hours (86,400 seconds) or max 1,000 events per tenant.
- Why: Covers typical client offline durations (e.g. overnight sleep) without incurring heavy database query overhead on reconnect.
- Option A (Suggested): 24 hours / 1,000 events limit. Balances complete offline catch-up with bounded database query size.
- Option B: 1 hour limit. Minimal database overhead, but clients offline longer than an hour miss history and require full inbox sync.
- Option C: Unlimited event retention replay. Risks excessive memory consumption and slow SSE connection establishment on ancient `Last-Event-ID` replay.

### Selection 3: Cloud Run Maximum Instance Count
- Title: Cloud Run _MAX_INSTANCES Configuration
- Suggested option: _MAX_INSTANCES: "3".
- Why: Enables horizontal scaling and high availability while keeping Cloud SQL connection pool requirements low.
- Option A (Suggested): _MAX_INSTANCES: "3". Provides horizontal scaling and proves multi-process durable delivery.
- Option B: _MAX_INSTANCES: "10". Handles higher concurrency spikes, but requires larger database connection pool tuning.
- Option C: _MAX_INSTANCES: "1". Retains single-point-of-failure risk and prevents multi-process scaling verification.

### Selection 4: SQLite Support for Events Table in Tests
- Title: Test Environment Database Compatibility Mode
- Suggested option: Dual-mode with SQLite condition emulation for unit tests and Postgres for integration tests.
- Why: Keeps unit test suite fast and dependency-free while validating real Postgres `LISTEN/NOTIFY` behavior in integration tests.
- Option A (Suggested): Dual-mode emulation. Fast unit test execution with full Postgres integration coverage.
- Option B: Require real Postgres for all unit and integration tests. Absolute dialect fidelity, but slows down local test execution and requires local Postgres setup.
- Option C: Polling-only mode for SQLite tests. Avoids custom condition emulation in unit tests, but test execution must wait for polling intervals.

## 7 Risks

- Cloud SQL Connection Pool Saturation: Multi-process `LISTEN/NOTIFY` connections use persistent database connections. Mitigation: Use dedicated pooled connections and keep `_MAX_INSTANCES` capped at 3.
- Event Table Growth: Unbounded growth of `events` and `nonces` tables over time. Mitigation: Implement TTL cleanup worker for `nonces` (300s TTL) and index-backed pruning policies for `events`.
- SSE Replay Storms: Large event backlog queries upon mass client reconnection. Mitigation: Enforce strict `LIMIT 1000` on replay queries.

## 8 Acceptance

- Gate checks pass cleanly: `python3 .github/gates/check_no_dashes.py` and `python3 .github/gates/check_client_data.py`.
- Events stored in `events` table survive API restarts and deliver to reconnecting SSE clients with `Last-Event-ID`.
- Multi-process integration tests (2 API processes, 2 subscribers, 2 workers) pass cleanly in pytest suite.
- ReplayGuard nonces persist across process restarts in the `nonces` table.
- Escalation and expiry execute reliably via leased database jobs without duplicate processing.
- `_MAX_INSTANCES` updated to `"3"` in `cloudbuild.yaml`.

## 9 Status

| Day | State | Evidence |
| --- | --- | --- |
| Day 1 | live | PR 92 merged, main 7afcd8ff, Cloud Build cd1579d7 SUCCESS, migrate job asserted head d4e5f6a7b8c9, revision hca-api-service-00068, smoke-live PASS (docs/deploys/2026-10-10_p20_day1_events.md). Content: migration d4e5f6a7b8c9 (events table, index tenant_id plus id), publish() writes the row before any queue and the envelope carries the row id as the SSE id line, replay() returns the rows a device missed (same tenant and routing set rule as live delivery, 24 h or 1,000 rows, env HCA_EVENT_REPLAY_WINDOW_SECONDS and HCA_EVENT_REPLAY_MAX_EVENTS), GET /v1/inbox/stream reads Last-Event-ID (header) or ?since= and replays before going live with no duplicate at the seam. Stream payloads are JSON-encoded once (ISO 8601 datetimes, matching the HTTP responses; before P20 the stream wrote str(datetime)). Proofs: tests/test_events.py (row written, monotonic ids, replay order, device and tenant filter, window and cap, header parsing), tests/test_inbox_api.py (reconnect replays missed events then live, no replay without the header, route parameters in the schema); 119 passed locally, alembic upgrade and downgrade on SQLite, check_migrated.py OK with 11 tables, openapi parity OK. Day 1 contributes nothing to fan-out yet: the queue set is still per instance (Day 2). |
| Day 2 | live | PR 93 merged, main 309183ec, Cloud Build 7fa02f4f SUCCESS, migrate job asserted head e5f6a7b8c9d0 (12 tables), revision hca-api-service-00070, smoke-live PASS, live /health shows events.listener connected (docs/deploys/2026-10-11_p20_day2_fanout.md). Content: table-driven dispatcher per event loop (wake plus 2 s poll, cursor at max id), pg_notify in the publish transaction, LISTEN thread with reconnect and poll fallback, nonces table shared replay guard, Mac app and extension send Last-Event-ID on reconnect (the extension reads with fetch, not EventSource, so the header is explicit). Proofs in tests/test_fanout.py; the Postgres-only listener proof runs in the CI Postgres job. |
| Day 3 | live | PR 95 merged, main e56c06af, Cloud Build c9271754 SUCCESS, migrate job asserted head f6a7b8c9d0e1 (13 tables), revision hca-api-service-00071, smoke-live PASS, live /health shows jobs.worker running (docs/deploys/2026-10-11_p20_day3_leased_jobs.md). Content: scheduled_jobs rows written with the ask (escalate per later rank, expire at expires_at), worker thread per instance with compare-and-set lease (FOR UPDATE SKIP LOCKED on Postgres), backoff and MAX_ATTEMPTS, reconcile for legacy and moved rows, sweep route as second trigger, P14 timers removed. Proof: four threads racing over twelve due jobs, each executed exactly once (tests/test_jobs.py). |
| Day 4 | live | PR 97 merged main 373fcf4f, Cloud Build d18a161a FAILED at pytest (two process suite read an expire job row before done_at); PR 98 merged main f16af4e1, Cloud Build 3c698080 SUCCESS, revision hca-api-service-00072, maxScale 3. |
| Day 5 | in_progress | scripts/restart_proof.py (API killed and restarted in the gap, reconnect replays each missed ask exactly once, 12 checks PASS), run in the smoke-local build step on every build; per device stream cap (HCA_STREAM_MAX_PER_DEVICE 4) and extension reconnect guard after the live metrics page showed 46 streams from one device; record docs/deploys/2026-10-11_p20_day4_day5_record.md. Open: the live owner proof (Notifier stopped, ask created, banner on restart). |
