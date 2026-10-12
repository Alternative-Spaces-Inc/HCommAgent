# Migration privileges and recovery

This runbook describes required provisioning and a recovery procedure. It does not assert that cloud IAM, database grants, a backup or a restore have been verified. Production changes are operator actions; no credentials are generated or printed by this document.

## Separate cloud and database identities

The runtime service uses `_RUNTIME_SA` and `_DB_URL_SECRET`. The migration job uses `_MIGRATE_SA=sa-hca-migrate` and `_MIGRATE_DB_URL_SECRET=hca-migration-database-url`. The source guard refuses identical account names or secret references. Operators must also verify that the secret resolves to a distinct database login: different Secret Manager names alone do not establish different privileges.

Give each service account Cloud SQL connection access and Secret Manager access to only its required secrets. The migration account needs its database secret; it does not need the admin token, sweep token or API pepper. The runtime account must not read the migration secret. The build identity needs narrowly scoped deployment/run permissions and permission to act as these two accounts. Avoid granting database DDL privileges to the runtime login merely to make a failed migration pass.

Use a dedicated product database/schema. A suitable role arrangement is a non-login DDL owner, a migration login allowed to act as that owner, and a runtime login with application-data privileges. Review these grants against the existing database before applying them:

| Identity | Required database access |
| --- | --- |
| DDL owner | Own the product schema, existing product tables/sequences and Alembic version table; create/alter/drop product objects |
| Migration login | CONNECT and the ability to assume the product DDL owner; execute advisory-lock functions; read/write the Alembic version table |
| Runtime login | CONNECT, schema USAGE, SELECT/INSERT/UPDATE/DELETE on application tables, sequence USAGE/SELECT; no schema CREATE, object ownership, role administration or session-termination grant |

Configure default privileges **for the role that actually creates migration objects**, so new application tables and sequences receive the runtime grants. Check existing ownership separately; a CREATE privilege does not permit altering a table owned by another role. Exclude the Alembic version table from runtime write grants. Revoke broad PUBLIC schema CREATE where the database policy permits. Use secure Cloud SQL/Secret Manager provisioning for login credentials instead of putting passwords in shell history or SQL files.

Verify by role, on an isolated restored database first: the migration login upgrades from the previously released head; the runtime login can use every API path and sequence; the runtime login cannot create/alter tables, write the Alembic version or read the migration secret. Compare actual grants and ownership with the table above and retain redacted evidence.

## Migration serialization and bounds

Every PostgreSQL upgrade takes the same session advisory lock before setup or DDL. `HCA_MIGRATE_SERIALIZE_SECONDS` defaults to 60, accepts only integers 1 to 300 and bounds waiting for another migration. `HCA_MIGRATE_LOCK_TIMEOUT` defaults to `60s`, accepts positive integer `ms`, `s` or `min` literals and is bounded to 1 millisecond to 300 seconds after unit conversion; zero is refused because PostgreSQL would disable the timeout. `HCA_MIGRATE_IDLE_TXN_GRACE` defaults to `30 seconds`, accepts positive integer `seconds` or `minutes` literals with one separating space, and is bounded to 1 to 86400 seconds. SQL receives canonical validated duration literals through bind parameters. The lock is held across Alembic's transactions and released on success/failure or connection close. Cloud Build creates a job named `${_MIGRATE_JOB}-${BUILD_ID}` and waits for that execution, so one build cannot replace another's migration image. The prefix must leave room for the UUID within Cloud Run's 63-character job-name limit. The job has no automatic retries and a 10-minute task limit. An owner-bound 45-minute service lease is acquired before the job and rechecked before deployment; the complete Cloud Build has a 30-minute timeout.

The immutable migration chain is `b8c9d0e1f2a3 -> 20261012_event_cursor -> 20261012_rate_limits`. Preserve applied migration files. Recovery repairs state or adds a new migration; it does not silently reorder this history. Schema validation after upgrade checks both the current head and expected tables.

Idle session termination is disabled in ordinary builds. Redacted blocker diagnostics identify backend IDs, application names, state and age; query text and row contents are not logged.

## Failed upgrade recovery

1. Stop new rollout attempts, retain the failed build's service lease, and inspect the failed execution and current schema head using the migration login. Determine whether the transaction rolled back or committed; a log line saying "Running upgrade" is not proof of applied DDL.
2. Confirm the serving revision still supports the observed schema. Keep traffic on the last verified compatible revision and preserve the failed candidate/job evidence.
3. Identify lock holders and their owners. Prefer cooperative session closure and fixing the source of a long transaction. Do not automatically terminate a connection merely because its application name is empty.
4. If an operator explicitly approves terminating stale API transactions, run a controlled migration/recovery execution with `HCA_MIGRATE_TERMINATE_IDLE=1` and `HCA_MIGRATE_RECOVERY_ROLE=<EXACT_RUNTIME_DATABASE_ROLE>`. Both the current database and `application_name=hca-api` plus that exact role must match; unnamed clients and other roles are excluded. This recovery identity needs explicit termination permission, which ordinary runtime/migration identities should lack. Revoke the temporary recovery grant afterward.
5. Repeat the upgrade from an isolated restored copy before retrying production. An advisory-lock timeout should be retried only after the previous job finishes or its connection is confirmed gone. Confirm head/table validation, inspect and prune unused candidate tags, and conditionally clear the terminal build's lease following the linked deployment runbook. Then start a new candidate build and repeat validation/smoke. Never clear a lease merely because its time label has expired while an external migration execution could remain active.

## Backup and restore proof

Before destructive schema changes, record the backup identifier/time, retention/encryption policy and an isolated restore target. Restore using a dedicated operator identity, confirm schema head and record counts, run the previous and proposed revisions against it, and exercise the approval/retry/receiver flow with synthetic records. Record measured recovery time and acceptable data loss with the responsible operator. A successful additive upgrade or rollback command is not a backup/restore drill. Production backup, IAM and restore evidence remain operator prerequisites.

For traffic rollback and candidate concurrency behavior, use [candidate deployment and traffic recovery](HCA_DEPLOY_ROLLBACK.md).
