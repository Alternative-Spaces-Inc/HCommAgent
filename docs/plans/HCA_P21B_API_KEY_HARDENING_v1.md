# HCA P21b: api key hardening, plan v1 (2026-10-12)

## 0 Summary

P21b builds the auth half of the P21 row of docs/plans/HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md, after P21a (docs/plans/HCA_P21A_WEBHOOK_SECURITY_v1.md) shipped the webhook half. Today an api key is all or nothing: a key that may create asks may also register webhook destinations, cancel any ask of its caller and read any of them; nothing bounds how fast a key may call; a leaked key can only be revoked, never rotated without a gap; and the Google token check sends an access token to Google in a query string. P21b adds per key scopes with a fixed vocabulary, per key rate limits enforced in the database so all Cloud Run instances share one view, key rotation with an overlap window on the P21a webhook secret pattern, audit rows for every key lifecycle event, and a rule that no credential ever travels in a query string, inbound or outbound.

Owner selections are listed in section 6 and recorded in docs/decisions/DECISIONS_20261012.md as decision 9 once taken.

## 1 Current behaviour and its failure modes

Verified against main c79821a8:

- `api/app/apikeys.py`: `issue_api_key` mints `hca_live_<prefix>_<secret>`, stores `key_hash` (HMAC-SHA256 under `HCA_API_KEY_PEPPER`) and `key_prefix`; `verify_api_key` looks the prefix up and compares in constant time. `models.ApiKey` (models.py line 114) has id, caller (unique), tenant_id, key_prefix, key_hash, created_at, revoked_at. No scope column, no limit columns, no rotation state.
- `api/app/auth.py` `require_agent_key` returns the row and every agent route accepts it: `POST /v1/requests`, `GET /v1/requests/{id}`, `POST /v1/requests/{id}/cancel` (requests_api.py) and the four `/v1/webhooks` routes (webhooks_api.py). A key issued so an automation can create asks can also revoke the caller's webhook destinations.
- `api/app/apikeys_api.py`: `POST /v1/api-keys`, `GET /v1/api-keys`, `POST /v1/api-keys/{prefix}/revoke`, all under the admin bearer. Revocation is immediate, so replacing a key means a window in which the caller has no working key, or two keys issued by hand with the old one forgotten.
- `caller` is unique on `api_keys`, so a second key for the same caller cannot exist today; rotation needs that constraint relaxed to (caller, active).
- No rate limiting anywhere (program plan section 1 finding, "holds"). One key can post asks as fast as the connection allows; the only cost is the row and the fan out.
- `api/app/googleauth.py` line 54 calls Google tokeninfo with `params={"access_token": ...}`: the token is in the request line and lands in any proxy or access log on the path. Inbound, no route reads a credential from the query string, but nothing rejects one either, so a misconfigured client that sends `?api_key=` leaks it into Cloud Run request logs.
- Key lifecycle events (issue, revoke) write no audit row; `webhook_destination_events` (P21a) is the pattern to copy.

## 2 Target design

- Scopes: fixed vocabulary, stored as a JSON list on the key row: `asks:create` (POST /v1/requests), `asks:read` (GET /v1/requests/{id}), `asks:cancel` (POST cancel), `webhooks:manage` (the four /v1/webhooks routes). `require_agent_key` becomes `require_scope(name)`; a key without the scope gets 403 with detail `api key lacks scope <name>` and the missing scope named in a `WWW-Authenticate: Bearer scope="<name>"` header. Issuance takes an optional `scopes` list; omitted means all four, so existing callers and the smoke script keep working. The migration backfills all four on existing rows. Admin `PATCH /v1/api-keys/{prefix}` may narrow or widen scopes; the change writes an audit row.
- Rate limits: two integers on the key row, `rate_per_minute` (every authenticated call, default 600) and `asks_per_minute` (POST /v1/requests only, default 60), plus one per tenant integer `tenants.asks_per_day` (default 0 meaning unlimited, settable by the platform admin). Enforcement is a fixed window counter table `rate_counters` (subject, window_start, count, unique on the pair) incremented with one `INSERT ... ON CONFLICT DO UPDATE ... RETURNING count` statement, so the three Cloud Run instances share one count and the hot path is one round trip. Over the limit answers 429 with `Retry-After` seconds to the window end and `X-RateLimit-Limit`, `X-RateLimit-Remaining` on every agent response. SQLite tests use the same statement through SQLAlchemy's upsert dialect support. Rows older than two windows are deleted by a `scheduled_jobs` kind `rate_sweep` every 10 minutes.
- Rotation: `POST /v1/api-keys/{prefix}/rotate` under the admin bearer, and `POST /v1/api-keys/self/rotate` under the agent key itself (any scope), so an automation can rotate its own credential on a schedule without the admin token. Both return the new key once. The old key stays valid until `previous_valid_until` (24 h, the P21a webhook overlap), then verifies as revoked. Implementation: a new row for the same caller with the new prefix; the old row gets `superseded_by` and `previous_valid_until`; the unique constraint on `caller` is replaced by a partial unique index on (caller) where `revoked_at is null and superseded_by is null`. `GET /v1/api-keys` shows both rows during the overlap with `state` active, overlapping, revoked, expired.
- Audit: table `api_key_events` (id, tenant_id, api_key_id, action issued, scopes_changed, rotated, revoked, expired, limit_hit, actor, detail JSON, at), written in the same transaction as the change; `limit_hit` is written at most once per key per window so a flood does not flood the table. Exposed read only under the admin bearer at `GET /v1/api-keys/{prefix}/events`.
- No credential in a query string: a middleware rejects any request whose query carries a key named `access_token`, `api_key`, `apikey`, `token`, `key` or `secret` (case insensitive) with 400 before routing and before the access log line is emitted with the query; the device signature canonical string is unaffected because the request never reaches the signed routes. Outbound, `googleauth.py` sends the token to tokeninfo as a form body field over POST (Google accepts both); the injectable fetcher signature changes to carry the token separately from the URL, and the fake in tests asserts the URL has no query.
- OpenAPI: `api/openapi.yaml` gains the scope names on the AgentKey scheme, the 429 response, the rotate and events routes, and the PATCH; the parity gate (P19) must stay green.
- Metrics: `/v1/internal/metrics` gains `api_keys` with active, overlapping and revoked counts and `rate_limited_last_hour`.
- Smoke: `scripts/smoke_golden_path.py` issues its key with the two scopes it needs, proves a 403 when it calls a webhook route with a key that lacks `webhooks:manage`, proves a 429 by issuing a key with `asks_per_minute` 2 and posting three asks, rotates its key and proves both the old and the new work inside the overlap, and revokes both at the end.

## 3 Build days

| Day | Branch | Work | Proof |
|-----|--------|------|-------|
| 1 | p21b/day1-scopes | migration `c9d0e1f2a3b4_api_key_scopes` (scopes JSON, rate columns, superseded_by, previous_valid_until, partial unique index, api_key_events, rate_counters, tenants.asks_per_day), `require_scope`, scoped issuance and PATCH, audit rows for issue, scope change and revoke, query string middleware, tokeninfo over POST | pytest: every agent route refuses a key lacking its scope and accepts one that has it; backfilled rows carry all four; `?api_key=` gets 400 with no route hit; fake tokeninfo sees a POST body and an empty query |
| 2 | p21b/day2-rate-limits | `rate_counters` upsert, limiter dependency on every agent route, 429 and headers, per tenant daily cap, `rate_sweep` job, `limit_hit` audit row, metrics block | pytest: 61st call in a minute is 429 with Retry-After; two threads racing the same key never exceed the limit; headers count down; sweep removes old windows; CI Postgres job proves the upsert on Postgres |
| 3 | p21b/day3-rotation-docs | both rotate routes, overlap semantics, states on the list route, events read route, OpenAPI updates and parity gate, docs/api-keys.md (scopes, limits, rotation runbook for an automation), smoke additions, ARCHITECTURE and README updates, deploy record | pytest: old key valid until previous_valid_until then refused; self rotate under any scope; smoke-local and smoke-live PASS with the new steps; live `GET /v1/api-keys` shows the smoke key rotated then revoked |

Each day ships as its own PR to main, merged by the owner, with the Cloud Build pipeline (guard, pytest, smoke-local, build, migrate, deploy, check-config, smoke-live) as the execution proof.

## 4 Risks

- The partial unique index replaces a plain unique constraint on a live table; the migration must drop the constraint on Postgres by its generated name and on SQLite rebuild the table (Alembic batch mode). Day 1 runs the chain against both engines in the test suite before the migrate job sees it.
- Fixed window counters allow up to twice the limit across a window boundary. Accepted for v1; a sliding window is a one statement change later if a real caller needs it.
- The query string middleware runs before authentication, so it must be cheap and must not read the body. It touches only `request.url.query`.
- Google tokeninfo over POST: documented as accepted; Day 1 proves it once against the live endpoint with an invalid token (expects 400 from Google, not 405) before the fake is updated.
- Rotation doubles the rows per caller during overlap; the `verify_api_key` lookup stays by prefix (unique), so no change to the hot path.

## 5 Status

Plan v1 drafted 2026-10-12 against main c79821a8. Awaiting owner selections in section 6. Days not started.

## 6 Owner selections

1. Scope vocabulary: fixed four scopes with all four as the default at issuance (suggested), or no scopes in v1 (rate limits and rotation only), or free form scope strings matched by prefix.
2. Rate limit defaults and enforcement: per key 600 calls and 60 asks per minute in a shared database counter with 429 and Retry-After (suggested), or per instance in memory counters (3 instances means up to 3 times the limit), or no limits in v1.
3. Rotation: admin rotate and agent self rotate, 24 h overlap (suggested), or admin rotate only, or immediate cut over with no overlap.
