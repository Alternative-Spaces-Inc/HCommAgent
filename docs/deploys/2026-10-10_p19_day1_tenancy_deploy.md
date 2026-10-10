# P19 Day 1 deploy record: tenancy basics live (2026-10-10)

## Result

- Live: Cloud Run revision hca-api-service-00052-6sp at 100 percent, main 2ce556ad, Cloud Build 29500a97 SUCCESS (deploy at 16:0x UTC).
- Database: Alembic head b7c8d9e0f1a2 (tenants, tenant_owners, tenant_id on asks, devices, api_keys, audit_rows), asserted by scripts/check_migrated.py inside the migrate job: "OK: database at head b7c8d9e0f1a2 with all 9 model tables present".
- smoke-live: all PASS (agent path, request ba30bba9).
- Existing devices kept working on the new schema: GET /v1/inbox signed calls returned 200 on revision 00052 at 16:09:18Z, 16:09:32Z, 16:10:22Z (tenant_id backfilled to the default tenant).

## Three builds to get here

| Build | main | Outcome | Cause |
| --- | --- | --- | --- |
| 736bf82f | 9aa2115f (PR 71) | FAILURE: migrate job hung 10 min, killed by the task timeout, DDL rolled back | Each open SSE stream held an idle-in-transaction backend with ACCESS SHARE on devices; ALTER TABLE devices waited behind them |
| 2eb3986f | f8a355e2 (PR 75) | FAILURE: migrate green, revision 00051 failed at startup, "relation tenants does not exist" | The SET lock_timeout statement ran outside the committed setup block, autobegan a transaction Alembic joined, and the whole upgrade rolled back on connection close while the log still read "Running upgrade" |
| 29500a97 | 2ce556ad (PR 76) | SUCCESS | Setup commits before Alembic; head asserted after upgrade |

Live traffic stayed on revision 00049 through both failures; no owner-visible outage.

## Fixes now on main

1. api/app/auth.py require_device sets expire_on_commit False on its own session before committing last_seen_at, so the attribute reads that follow (google_sub, id, tenant_id) do not re-select the row and open a transaction the stream then holds for its whole life. Regression test test_device_dependency_leaves_no_open_transaction fails without the line and passes with it. A global expire_on_commit flag was tried and rejected: it changed response datetime shapes (aware in memory, naive from the database) and broke test_apikeys.
2. api/alembic/env.py on Postgres: one committed transaction that lists and terminates backends idle in transaction longer than 30 s on this database (HCA_MIGRATE_TERMINATE_IDLE=0 disables) and sets lock_timeout 60 s (session scoped, survives the commit); an assertion that no transaction is open before Alembic configures; on lock timeout, every backend with an open transaction is printed instead of hanging until the job timeout. On build 29500a97 the terminate step found nothing to clear.
3. api/scripts/check_migrated.py compares the alembic_version row with the head in alembic/versions and checks every model table exists. Runs in the Postgres CI job after alembic upgrade head and in the Cloud Run migrate job (sh -c "alembic upgrade head && python3 scripts/check_migrated.py"), so a rolled back upgrade fails the build before deploy.
4. api/app/db.py engine pool_pre_ping, so a connection the migrate job terminated is replaced quietly.

## Lessons

- The API tests call Base.metadata.create_all after alembic upgrade head, so CI never proved the upgrade committed. Any check of a migration must read database state, not the Alembic log.
- Merge one PR, wait for its build, read the migrate job log and the live revision before the next merge.
- A green "Finished Step" for a job is not proof; read the job's own log lines.

## Open

- PR 72 (p19/day2-state-machine, adb0b27) and PR 74 (p19/day3-openapi-parity, 99d3920) target main and need a rebase onto 2ce556ad before their checks are meaningful.
