---
title: HCA production hardening program, plan v1
summary: Verified gap inventory against main fca6eb01 and a phased program (P19 to P27) that turns HCA into a secure human control plane for agentic retail, marketing, merchandising and creative operations workflows.
---

# HCA production hardening program, plan v1 (2026-10-09)

Source: an owner-supplied hardening brief received 2026-10-09 14:5x PT
(the brief itself is not in the repo). Every claim in the brief was
checked against main fca6eb01 before it entered this plan; the table
below says which held. Positioning target:
a secure human control plane for agentic retail, marketing, merchandising
and creative operations workflows. Compatibility signals for a retailer's
stack (Box, Figma, Google Workspace, content and campaign systems, webhook
automation) are served through connector interfaces with mocked reference
implementations; nothing in this repo claims what any company runs.

## 1. Claim inventory (brief versus repo)

| Brief claim | Repo evidence (main fca6eb01) | Verdict |
| --- | --- | --- |
| SSE sends every event to every device, clients filter on visible_to | api/app/events.py publishes to all subscriber queues; api/app/inbox_api.py line 54 subscribes without a device filter; routing.py line 11 documents data.visible_to for clients | holds, P0 |
| Single instance forced | cloudbuild.yaml line 38 `_MAX_INSTANCES: "1"` with the comment "in-process event bus" | holds |
| Replay guard is process local | api/app/devicesig.py lines 24 to 25 and 92 to 96, in-memory (device, nonce) set | holds, P1 |
| Escalation and expiry timers in process | api/app/main.py line 23, routing.py line 110 (asyncio timers) | holds |
| Answer accepts a control that does not match the ask | requests_api.py line 150 validates body.control, never compares to ask.control | holds, P1, test to add |
| expires_at can be null on an expired ask | models.py line 33 nullable, no default on create | holds |
| Documented endpoint returns 404 | api/openapi.yaml line 178 /v1/controls, no route in api/app | holds |
| PROPOSED label on an implemented endpoint | openapi.yaml line 351, api keys shipped in P3 and P10 | holds |
| Webhooks lack SSRF guard, HTTPS only, HMAC, durable outbox | webhooks.py: httpx client, in-process retries "1,3" seconds, no destination check, no signature | holds, P0 for SSRF |
| Container runs as root | api/Dockerfile has no USER line | holds |
| Extension needs optional https://*/* | manifest optional_host_permissions ["https://*/*"], granted per saved origin at runtime (P11) | partly: scope is runtime narrowed, manifest wording stays broad |
| Mac app has test notification menu items | macos/Sources/HCANotifier/main.swift lines 88 to 89 | holds |
| No rate limiting, no structured logging, no correlation ids | no limiter or logger usage in api/app | holds |
| Privacy text claims retention it does not enforce | api/app/privacy.py line 50 "until the owner deletes them", no delete or export route | holds |
| Bootstrap tooling mixed into the product | bootstrap/, agents/, platform/ ship in the same repo as api/ | holds, decision needed |
| Developer ID signing and notarization missing | P16 self-signed identity, P18 Path B LaunchAgent because of no Team ID | holds, needs the Apple Developer Program (owner spend) |

Not accepted from the brief: Redis (Cloud SQL Postgres is already the
system of record; one durable store keeps the design honest), a vendor
specific agent SDK in the core, naming any company's internal systems.

## 2. Decisions the owner takes before P19 starts

1. Tenancy: single owner per deployment (today) with an explicit
   owner_scope on every record and every query, or full multi-tenant.
   Suggested: single owner per deployment, owner_scope enforced
   everywhere so a second tenant is a data change, not a rewrite.
2. Public framing: generic "agentic retail operations" wording in the
   repo, no company named anywhere. Suggested: generic.
3. Bootstrap tooling: move bootstrap/, agents/ and platform/ to a
   separate operations repo (owner creates it) or harden in place.
   Suggested: separate repo; this repo keeps a README pointer.
4. Apple Developer Program (99 USD per year, owner spend) for Developer
   ID, hardened runtime and notarization in P24. Without it P24 ships
   the hardening and documents the release path as blocked on the
   membership, never as production ready.

## 3. Phases

Each phase: plan section confirmed by the owner, one or more PRs, gates
green, record under docs/deploys/, owner merges. No phase starts on a
red main. Ordered by risk: server authorization first, product
workflow fourth, clients and release later.

| Phase | Scope | Proof |
| --- | --- | --- |
| P19 Authorization and request state machine | per-device SSE filtering on the server before enqueue, owner_scope on asks, audit, devices; answer must match ask.control; strict per-control value schemas (radio, checkbox, approve_deny, dropdown, chat, confirm_rows, voice, zoom); option uniqueness, size limits, UTC normalisation, deadline ordering, expires_at always set; explicit state transitions with SELECT FOR UPDATE so one terminal transition wins; idempotency keys on create, answer, cancel; deterministic 409; /v1/controls implemented; PROPOSED removed; OpenAPI generated from FastAPI with a CI parity gate | two owners two devices tests prove no leakage; concurrency tests answer versus cancel, answer versus expiry, duplicate answer, duplicate cancel, duplicate retries |
| P20 Durable delivery | events table (bigserial id, type, request_id, schema_version, owner_scope, visible device ids) as the SSE source with Last-Event-ID replay; LISTEN/NOTIFY fan out, poll fallback; nonce table with TTL cleanup; escalation and expiry as leased DB jobs; per-stream authorization recheck; metrics for lag, drops, replay gaps; `_MAX_INSTANCES` to 3 | restart test: events published while the API is down are delivered on reconnect; two API processes, two subscribers, two workers in the integration test |
| P21 Webhooks and auth | HTTPS only, SSRF guard (loopback, private, link local, metadata, IPv6 forms, DNS rebinding by resolving once and pinning), no redirects, size and time limits, durable outbox with lease and jittered backoff, HMAC per destination with rotation, timestamp header, dedupe; API key scopes, rate limits, rotation; audit events for registration and revocation; token never in a query string | tests: internal destinations blocked, signature verifies, retry survives restart, duplicate outcome deduplicated |
| P22 Work orders, orchestration interface, connectors, demo | WorkOrder and WorkflowStep models (workflow_id, work_order_id, step_id, correlation_id, schema_version, idempotency_key, requested_by, owner_scope, required_capabilities, affected_resources, human_decision, result_artifacts, audit_reference); provider neutral `POST /v1/work-orders` and `POST /v1/work-orders/{id}/steps/{step}/resume`; connector interface plus mocks for Google Drive, Box, Figma, a content and campaign system, generic webhook; `scripts/demo_retail_workflow.py` runs with SQLite or Postgres and a simulated device, no credentials; docs/ARCHITECTURE.md; README section "Architecture for Agentic Retail Operations" | demo transcript recorded: create, validate, gate, approve from a client, resume, mock connector update, audit read, retried step causes no second effect, unauthorized device sees nothing |
| P23 Chrome extension hardening | HTTPS origin validation, single flight device key creation, key rotation, deterministic canonical serialisation, durable race free offline queue with TTL and size limits, controls disabled during submit, idempotent clicks, deterministic notification click path, worker restart safety; browser integration tests (Playwright) for restart, offline, reconnect, duplicate clicks, concurrent queue writes, notifications, permission denial, malformed events | CI job runs the browser suite headless |
| P24 macOS hardening and release path | strict concurrency, warnings as errors, actor isolated state, test menu items behind a debug flag, API base URL validation, percent encoded path components, OAuth redirect parsing hardened, key rotation and revocation, durable notification metadata, bounded categories; release path with Developer ID, hardened runtime, notarization, entitlements check, reproducible artifact, version and update strategy (blocked on decision 4) | swift build with -warnings-as-errors in CI on a macOS runner; notarization receipt if decision 4 is yes |
| P25 Containers, deployment, CI | non-root image, pip hashes lockfile, image scan, SBOM and provenance, readiness and liveness routes, graceful shutdown, image digest pins, migration upgrade test from the previous schema, coverage threshold, secret scanning, structured logs with correlation ids, rate limiting, alerts | every new job green on main; smoke after deploy unchanged |
| P26 Bootstrap isolation | per decision 3: move to the operations repo with scoped credentials, exact command approvals with nonces, allowlists and audit logging; this repo loses privileged tooling | gate: no file under bootstrap/ or platform/ in the product tree |
| P27 Documentation and policy | SECURITY.md, CODEOWNERS, LICENSE, threat model, incident response, key rotation, webhook compromise, data export and deletion routes matching the privacy text, runbooks updated | docs gates green; privacy text equals the implemented behaviour |

## 4. Red gates (owner)

- Decisions 1 to 4 above.
- Every PR merge, every Cloud Run deploy, the `_MAX_INSTANCES` change.
- Apple Developer Program spend (P24).
- Creation of the operations repo (P26).

## 5. Acceptance for the program

1. Two owners, two devices: no cross-owner title, option, answer,
   webhook or audit data in any stream, inbox or audit read (test).
2. Two API instances behind Cloud Run, one subscriber per instance,
   both receive every event once; a restart loses none (test and a
   live proof).
3. Every answer, cancel, expiry and escalation race leaves one terminal
   audit row and one terminal event (tests).
4. The retail demo runs end to end locally without credentials and
   its transcript is in docs/deploys/.
5. CI: Postgres tests, migration upgrade test, browser suite, Swift
   build with warnings as errors, OpenAPI parity, scans, coverage.
6. No P0 or P1 from the inventory remains open; anything blocked on an
   owner decision is listed with the decision, not as future work.
