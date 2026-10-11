# P21a Day 1 deploy record: webhook destination registry and SSRF rules

Date: 2026-10-11, 16:2x PT. Plan: docs/plans/HCA_P21A_WEBHOOK_SECURITY_v1.md,
decision 7 in docs/decisions/DECISIONS_20261011.md.

## Evidence

| Item | Value |
| --- | --- |
| PR | 110, head a5e07d4, merged by the owner |
| main after merge | 2a7d7093 |
| Cloud Build | 347d1e33 SUCCESS (gates, smoke-local with the loopback switch, restart proof, deploy, migrate, smoke-live) |
| Migrate job | hca-migrate-lvfvj, succeeded 1, log line "OK: database at head a7b8c9d0e1f2 with all 15 model tables present" |
| Live revision | hca-api-service-00076 at 100 percent |
| Live probe | GET /v1/webhooks without a key: 401; /health db ok, listener connected, jobs worker running |
| Tests | 197 passed, 2 skipped (SQLite); API Tests (Postgres) check run success; ruff clean |

## What is live

- Callers register webhook destinations once under their api key
  (POST /v1/webhooks); the whsec_ secret is returned once and stored as an
  HMAC-SHA256 hash under the api key pepper. List, rotate (24 h overlap) and
  revoke routes; every change writes a webhook_destination_events row.
- Destination URLs pass the SSRF rules at registration (https, no userinfo,
  port 443 or above 1023, no metadata host, no loopback, private, link local,
  multicast, reserved or unspecified address by literal or by resolution,
  IPv4 mapped, 6to4 and Teredo forms unwrapped). Resolved addresses are
  pinned on the row.
- An ask whose deliver_to.mode is webhook and whose url is not a registered,
  non revoked destination of the same caller and tenant is rejected with 400.
  The ask stores destination_id for Day 2 signing.
- The smoke-local pipeline step runs with HCA_WEBHOOK_ALLOW_INSECURE=1 so its
  loopback receiver passes; the deploy step does not set it.

## Not yet live (Day 2 and 3)

Signing headers (X-HCA-Signature, X-HCA-Timestamp, both secrets during a
rotation overlap), the send time re-check of the resolved address, the
durable outbox on scheduled_jobs with the 7 attempt schedule and dead marking,
delivery status in the request view, replay of the dead letter.

## Lessons

- A fake resolver for the suite must answer a globally routable address: the
  documentation ranges (203.0.113.0/24) count as non global for the
  ipaddress module and the rules reject them.
- Tests that run a real server process (sse_e2e, two_process) resolve DNS
  for real; they post poll mode asks now, since they test the stream.
- The git stash and a tracked pycache file collided during the Day 1 commit;
  the pycache line should leave the index (follow-up).
