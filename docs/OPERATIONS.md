# Operations and recovery

This runbook describes the remediation candidate. No production cloud deployment, IAM change, backup restore or live load test is established by updating this repository. Operators must review the exact revision, provision the required identities/secrets and collect the evidence below before a production rollout.

## Production prerequisites

Use `HCA_ENV=production`. Startup rejects a non-Postgres database, short/missing admin/sweep/pepper credentials, a disabled Google link gate, missing Google OAuth client audiences or insecure webhooks. Configure current permitted tenant owners before linking devices. An explicitly empty owner list grants nobody access.

| Configuration | Purpose |
| --- | --- |
| `DATABASE_URL` | Production Postgres with a runtime database principal restricted to application operations |
| `HCA_ADMIN_TOKEN` | Separate platform-administration credential, at least 32 characters in production |
| `SWEEP_TRIGGER_TOKEN` | Separate scheduler credential, at least 32 characters in production |
| `HCA_API_KEY_PEPPER` | Current caller-key HMAC verification pepper; provision/rotate independently of webhook encryption |
| `HCA_REQUIRE_GOOGLE_LINK=1` | Require linked, currently permitted owners |
| `HCA_GOOGLE_CLIENT_ID` or `HCA_GOOGLE_CLIENT_IDS` | Accepted OAuth audiences for configured clients |
| `HCA_WEBHOOK_ENCRYPTION_KEYS`, `HCA_WEBHOOK_CURRENT_KEY_ID` | Dedicated versioned webhook encryption keyring and active encryption key |
| Database pool settings | Bound per-instance pool/overflow against the database connection budget |
| `_MIGRATE_SA`, `_MIGRATE_DB_URL_SECRET` and migration database credentials | Separately provisioned migration service account and database principal; do not reuse runtime DDL privileges |

The current cloud pipeline requires the dedicated migration identity and new encryption secret configuration before rollout. Repository substitutions cannot create cloud accounts, grants or Secret Manager versions. Give the runtime identity only the data, secrets and cloud access it needs; give the migration identity only the migration privileges it needs. Record actual IAM/database grant evidence privately and publish a sanitized verification result.

Never enable `HCA_WEBHOOK_ALLOW_INSECURE` in production. The local mock demonstration uses explicit development mode and loopback endpoints; it is not evidence for production Google authorization. Protect the admin/sweep tokens outside clients, logs, source and command transcripts.

## Candidate rollout and rollback

1. Pin the source revision and record its passing checks, dependency audit, CodeQL coverage, image scan and SBOM/provenance. Retain artifact checksums and API/client compatibility information.
2. Check the actual source-upload manifest includes extension files required by packaging. Provision separate migration identity/database credentials and versioned encryption secrets; validate fail-closed production configuration.
3. Back up the database and verify the restore procedure before schema changes. Test upgrade from the deployed schema and compatibility with the old/new application revisions overlapping during rollout.
4. Run migrations with bounded locks and assert schema head/table presence. Automatic idle-transaction termination is disabled by default. Any exceptional recovery must target the application's scoped sessions and omit query contents from logs.
5. Deploy a build-specific candidate tag with no production traffic. The pipeline captures that tag's exact revision and promotes that revision after its candidate smoke. Inspect that candidate's effective environment/secret references and run its health/agent smoke against the candidate URL.
6. Perform the separate real Google-linked human approval and signed-receiver check with an isolated authorized test identity/destination. Check active streams reconnect and owners/devices removed during traffic lose access.
7. Promote only the validated candidate. Serialize deployments so another build cannot change the candidate/ready revision between validation and traffic promotion. Record the source/image/revision identities and previous traffic target.
8. If health, authorization, data integrity or delivery degrades, return traffic to the previously verified compatible revision. A schema rollback is a separately reviewed operation; do not assume application traffic rollback reverses migrations.

The source pipeline stages candidate deployment, configuration checking, agent smoke and promotion. Its automated live smoke covers the agent create/cancel path, not an actual Google-linked approval or all client behaviors. Cloud trigger/deployment serialization, credential provisioning, actual revision promotion and rollback evidence remain operator responsibilities. The [first-deployment runbook](runbooks/HCA_FIRST_DEPLOY.md) is historical setup context; reconcile its details with this runbook and the current pipeline.

## Credentials and key rotation

For a suspected caller-key compromise, revoke that caller's key, investigate affected requests/destinations and issue a replacement through the admin route. Preserve the caller/tenant audit context. For a device compromise, revoke the server device first, remove owner membership if appropriate, rotate the local client key, register the new public key and link a current owner. Chrome's local rotation discards offline responses and does not itself revoke the old server registration. Mac Keychain cleanup changes device identity and requires registration/linking again.

For an admin or sweep credential compromise, stop its use, replace the relevant Secret Manager version/configuration, roll compatible instances, and verify the old bearer is refused. Investigate issuance, owner/device changes, exports/purges and sweep activity. If an administrator or database was compromised, re-establish trusted state from verified records; client signatures cannot make a malicious administrator trustworthy.

Receiver signing-secret rotation uses `POST /v1/webhooks/{id}/rotate` and a 24-hour old/new signing overlap. Install the new secret on the receiver before ending overlap. Confirm signatures and duplicate-ledger behavior with synthetic delivery data. Revoke a destination when it is no longer trusted.

Webhook encryption-key rotation uses a **separate versioned keyring**. Add the new key and select its key ID for new encryption, explicitly re-encrypt existing destinations, verify old/new decrypt behavior and retain old decrypt keys until retained ciphertext and necessary backups have a migration/expiry path. Legacy ciphertext was derived from the old API pepper; preserve that pepper only for the controlled migration. Use `python scripts/rotate_webhook_encryption.py` for a dry-run and add `--apply` only after reviewing counts and your backup. Configure the keyring as a JSON object mapping key IDs to base64url-encoded 32-byte keys; `HCA_WEBHOOK_CURRENT_KEY_ID` selects a present ID. `HCA_API_KEY_PREVIOUS_PEPPERS` is a JSON list of up to three prior verification peppers. Review tool help/output; do not modify ciphertext or key identifiers by hand.

API pepper rotation is separate. The implementation supports a bounded previous-pepper verification overlap so existing caller credentials can be migrated/reissued before the old pepper is retired. An overlap does not invalidate a stolen caller secret. Rotate compromised caller keys directly and re-verify that old secrets fail. Record pepper/key IDs and migration counts without publishing key material. Do not delete the final old decrypt/verification material until backup and destination migration has been tested.

## Retention, export and deletion

The scheduler sweep removes transient events and completed jobs older than seven days in bounded batches. Replay availability remains 24 hours with a count bound; physical retention and replay availability are different. Expired retry/nonce/rate state is cleaned according to its bounded policy. Decision audits are not automatically discarded by transient cleanup.

`GET /v1/tenants/{tenant_id}/records` is an admin-protected, paginated export. Follow `X-HCA-Next-Cursor` with `after`; the page cap is 500. `POST /v1/tenants/{tenant_id}/records/purge` requires the tenant slug, an explicit timezone-aware cutoff and a bounded batch limit. It deletes completed request records and associated delivery/audit/event/retry data. Export and approve the intended cutoff first. Active requests must be cancelled before deletion. Purging removes evidence needed for delivery disputes and can remove pending outbox work for a terminal request; reconcile receiver effects before applying retention.

Owner/device profiles, destination registry/audit, backups and infrastructure logs need an operator-defined retention/deletion policy. Deleting running-database records does not immediately erase backups. Document operator contact, restore-safe retention windows and data-subject handling for the actual deployment. The served `/privacy` page explains the product behavior; a deployment must supply its own accurate operator details.

Chrome offline responses are capped at 100 entries, 32 KiB per answer, 1 MiB total, 20 attempts and 24 hours or an earlier non-hold deadline. Origin changes and key rotation discard old scoped data with a notice. Both clients clear sensitive pending state when authorization is refused. Native notifications and user-enabled previews can remain in OS-managed storage and require user cleanup.

## Monitoring and incident response

Use admin-protected internal metrics and redacted logs to monitor worker/listener health, replay gaps, dropped subscriber queues, expired leases, webhook queued age/dead counts and authentication failures. Keep request/delivery/job identifiers for correlation without logging bearer credentials, access tokens, private keys or full sensitive decisions.

Set and test thresholds against the deployed load. The source counters are not an installed alerting service. Watch tenant/actor rate-limit responses, storage growth, connection saturation and repeated client delivery notices. Four local stream slots per device do not establish a global cross-instance quota; bound instance counts/edge traffic and test abuse controls. No throughput, availability, cost or SLO figure is claimed without a measured workload/environment.

When an incident occurs, restrict or revoke the affected identity/destination, preserve sanitized evidence, identify the last trusted revision/state, repair the source/configuration and replay only through an approved deduplication-aware recovery path. Dead deliveries need investigation of receiver acknowledgements and effect ledgers before recreating work. Report product vulnerabilities through [private reporting](../SECURITY.md); avoid public personal/customer data.

## Evidence still required

Before advertising a production rollout, retain a dated record of candidate/previous revision, migration principal/grants, schema upgrade, validated config, exact CI/scan results, real owner sign-in and approval, signed receiver dedupe, busy-stream revocation, traffic promotion/rollback, restore drill and load/soak/connection budget. Record native notification, launch-at-login and Keychain/OAuth checks on the target Mac and Chrome OAuth/scheduling/store checks on the target browser.

Public notarized Mac distribution is outside the developer-only scope. If added, it needs Developer ID signing, hardened runtime/entitlements, notarization, install/upgrade/uninstall and update-path evidence. Creating a private operations repository, rewriting history or deploying cloud resources is separate work; none is established by removing privileged tooling from this product tree.
