# HCA P21a: webhook security and durable outbox, plan v1 (2026-10-11)

## 0 Summary

P21a builds the webhook half of the P21 row of docs/plans/HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md. Today an ask names any URL in `deliver_to.url`, the server POSTs the outcome to it unsigned from a FastAPI background task, retries twice in process (1 s, 3 s) and gives up; a restart between attempts loses the delivery, and nothing stops a caller from pointing the service at a loopback, private or metadata address. P21a introduces registered webhook destinations with a per destination HMAC secret, an SSRF guard applied at registration and again at send time on the pinned address, HTTPS only with no redirects and bounded response size and time, a durable outbox on the P20 Day 3 `scheduled_jobs` table with jittered backoff over about one hour, and a delivery id the receiver can deduplicate on. The API key half (scopes, rate limits, rotation) is P21b.

Owner selections (decision 7 in docs/decisions/DECISIONS_20261011.md): an ask whose `deliver_to.url` is not a registered destination of the caller is rejected with 400; the outbox retries 7 times over about one hour, then marks the delivery dead.

## 1 Current behaviour and its failure modes

Verified against main bcb1a18a:

- `api/app/webhooks.py` `deliver_outcome` (lines 104 to 165): any `deliver_to.url` is POSTed with `httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)`; no scheme check, no address check, no signature. httpx does not follow redirects by default, which is the only guard in place.
- Retries are a `for attempt in range(1, max_attempts + 1)` loop with `time.sleep` inside the background task (`WEBHOOK_RETRY_DELAYS` default `1,3`). A process recycle during the sleep loses the remaining attempts; the attempt rows already written say nothing about what was still owed.
- `WebhookAttempt` rows (models.py lines 225 to 239) record each POST, but the response body is never bounded and a slow destination holds the worker for the full timeout per attempt.
- `DeliverTo` (schemas.py line 50) accepts `mode` and a free `url`. There is no destination registry, so there is nothing to revoke, rotate or audit per destination.
- The smoke test (`scripts/smoke_golden_path.py`) receives the webhook on a loopback HTTP server, which the SSRF guard must block in production. The local and pipeline smoke runs need an explicit insecure switch that the production deploy never sets.

## 2 Target design

- Destination registry: table `webhook_destinations` (id `whd_` prefix, tenant_id, caller, url, secret_hash HMAC-SHA256 under the api key pepper with the same `hmac256$` scheme as api keys, secret_prefix, pinned_ips JSON, created_at, revoked_at, last_rotated_at). Routes under the caller's api key: `POST /v1/webhooks` (url; returns the destination id and the secret once), `GET /v1/webhooks`, `POST /v1/webhooks/{id}/rotate` (new secret returned once; the old secret stays valid for 24 h so the receiver can switch, both signatures sent as v1 entries during the overlap), `DELETE /v1/webhooks/{id}` (revoke). Each registration, rotation and revocation writes an audit row.
- Validation at registration (module `app/ssrf.py`): scheme https only; host is not an IP literal in loopback, private, link local, multicast, reserved, unspecified ranges, and not an IPv4 mapped or 6to4 IPv6 form of one; host resolves (A and AAAA) and every address passes the same checks; port 443 or an explicit port above 1023; no userinfo in the URL; the metadata host names used by cloud providers are rejected by name as well. The resolved addresses are pinned on the row.
- Validation at send: the destination is re-resolved; the send proceeds only to an address that passes the checks and is connected to directly (httpx transport bound to the resolved IP with the Host header and TLS SNI set to the registered host name), so a DNS answer that changes between registration and send cannot redirect the POST (DNS rebinding). `follow_redirects` stays off; a 3xx is a failure. Response body read is capped at 64 KiB, connect timeout 3 s, total 10 s.
- Signing: headers `X-HCA-Delivery-Id` (one id per outcome, stable across retries), `X-HCA-Timestamp` (unix seconds), `X-HCA-Event`, `X-HCA-Request-Id`, `X-HCA-Signature: t=<ts>,v1=<hex>` where v1 is HMAC-SHA256 under the destination secret over `<ts>.<raw body>`. Receivers reject a timestamp older than 5 minutes and deduplicate on the delivery id. A verification snippet (Python and Node) ships in docs/webhooks.md.
- Durable outbox: a `webhook_deliveries` table (id `whdl_` prefix, tenant_id, ask_id, destination_id, event, payload JSON, state queued, delivered, dead, attempts, next_run_at, delivered_at, dead_at) and a `scheduled_jobs` kind `webhook` pointing at the delivery. The job runner (jobs.py) claims it with the existing lease, performs one attempt, writes a `webhook_attempts` row, and on failure reschedules itself at the next backoff step with up to 20 percent jitter: 1 s, 5 s, 30 s, 2 min, 10 min, 30 min, 1 h. After the 7th failure the delivery is dead; a `webhook.dead` audit row records it. The FastAPI background task is removed; answer, cancel and expire enqueue a delivery in the same transaction as the state change.
- Ask creation: `deliver_to.mode` webhook requires `deliver_to.url` to equal a non revoked destination url of the same caller and tenant, otherwise 400 with detail `deliver_to.url is not a registered webhook destination`. The destination id is stored on the ask (`deliver_to.destination_id`) so a later URL change on the destination does not retarget old asks silently; revoked destinations fail their deliveries dead with the reason recorded.
- Local and pipeline smoke: env `HCA_WEBHOOK_ALLOW_INSECURE=1` permits http and loopback for the smoke receiver. The deploy step never sets it; `scripts/check_config.py` (new) fails the pipeline if the live service env carries it. The smoke script registers its destination, verifies the signature on the received payload and revokes the destination at the end.
- Metrics: `/v1/internal/metrics` gains `webhooks` with queued, delivered, dead counts and oldest queued age.

## 3 Build days

| Day | Scope | Proof |
| --- | --- | --- |
| 1 | `app/ssrf.py` with the address rules and the pinned send transport; `webhook_destinations` migration and routes; decision 7 recorded | pytest: every forbidden address class rejected (IPv4, IPv6, mapped, rebinding via a fake resolver), https only, registration returns the secret once, rotate overlap, revoke; audit rows present |
| 2 | Signing headers and `webhook_deliveries` outbox on `scheduled_jobs`; answer, cancel, expire enqueue; smoke script registers, verifies and revokes; `HCA_WEBHOOK_ALLOW_INSECURE` and `check_config.py` | pytest: signature verifies with the documented snippet, retry survives a simulated restart (worker stopped between attempts, restarted, delivery completes), 7th failure marks dead, duplicate delivery id on retry; smoke-local and pipeline PASS |
| 3 | docs/webhooks.md, openapi.yaml, README, extension and Mac unaffected check; deploy record; live proof with a public receiver the owner controls or a request bin the owner selects | live: signed delivery received once, SSRF probe against a private url rejected with 400 at registration, metrics webhooks counts |

## 4 Risks

- A caller's existing asks with raw urls: none in production (only smoke asks), so no migration of old `deliver_to` values beyond leaving them unsigned and undeliverable after the change; the record names this.
- Pinning the connection to a resolved IP with httpx needs a custom transport (`httpx.HTTPTransport` with a mounted resolver, or an `local_address` style trick); the fallback is to resolve, check, then connect by host name and re-check the peer address from the socket after connect, failing closed on mismatch.
- The 1 h backoff tail means a dead marking can arrive 1 h 43 min after the outcome at the earliest; the owner accepted this over the 24 h option.

## 5 Status

| Item | State |
| --- | --- |
| Plan v1 | this file |
| Decision 7 | docs/decisions/DECISIONS_20261011.md |
| Day 1 | live 2026-10-11 16:2x PT: PR 110 merged main 2a7d7093, Cloud Build 347d1e33 SUCCESS, migrate job hca-migrate-lvfvj asserted head a7b8c9d0e1f2 (15 tables), revision hca-api-service-00076, smoke-live PASS, live GET /v1/webhooks without a key 401 |
| Day 2 | live 2026-10-11: PR 111 merged (main b58c3cfa), build ea8c4731, revision 00078, head b8c9d0e1f2a3 (16 tables); decision 8; record docs/deploys/2026-10-11_p21a_day2_outbox.md |
| Day 3 | not started |
