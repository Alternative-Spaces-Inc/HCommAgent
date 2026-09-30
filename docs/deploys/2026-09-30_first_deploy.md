# First deploy record, 2026-09-30

Runbook: docs/runbooks/HCA_FIRST_DEPLOY.md, run end to end by Al (approvals) and HCA (execution through the Mac bridge). Project id, region and service URL are runbook values held by the owner, not recorded here.

## What is live

| Item | Value |
| --- | --- |
| Source commit | bb9653b52adf8b164e95856e90c549f1cb0e4c27 (main, after PR 17) |
| First build | 22e87326-7e69-4d3a-b450-bbc12d56a04f on d4c4ac0a5b10, SUCCESS, 2m19s |
| Redeploy build | 189266f9-7e7c-4aed-8a07-8d74da36a672 on bb9653b52adf, SUCCESS, 2m46s |
| Service revision | hca-api-service-00002-dqx at 100 percent, runtime account sa-hca-api |
| Migrate job | hca-migrate, execution hca-migrate-d7whl succeeded 22:04:12Z |
| Database | Cloud SQL Postgres 16, Enterprise, db-f1-micro, instance hca-pg, database hca, user hca |
| Secrets | hca-database-url, hca-sweep-trigger-token, hca-admin-token (one version each) |
| Scheduler | hca-expiry-sweep, */5 * * * * UTC, ENABLED |

## Verification (from outside, 22:35Z to 22:40Z)

- GET /health: 200 {"status":"ok","db":"ok"}
- POST /v1/requests, POST /v1/api-keys, POST /v1/internal/expiry-sweep without a bearer: 401
- GET /v1/inbox/stream: 200 text/event-stream
- Scheduler: manual run 22:38:12Z POST 200 (Google-Cloud-Scheduler), first tick 22:40:04Z POST 200

## Timing

Cloud SQL create took 14 minutes (21:33Z to 21:47Z). Everything else ran in under 3 minutes per step.

## Defects found and fixed during the run (PR 17)

1. Step 3 role grants raced the new service account; the runbook now pauses 20 s.
2. gcloud defaulted Postgres to Enterprise Plus, which rejects db-f1-micro; the runbook now passes --edition=enterprise.
3. From the public URL the Google frontend answers exactly /healthz with an HTML 404; the API now also serves /health and external checks use it.

## Open, by design until the owner-auth packet

GET /v1/inbox, GET /v1/inbox/stream, POST /v1/requests/{id}/answer and GET /v1/audit accept unauthenticated calls (see api/app/auth.py). The service URL is unlisted but public. Until that packet lands, do not point the extension at the live service for anything but empty-inbox smoke tests, and treat the database as containing nothing an anonymous reader may not see.
