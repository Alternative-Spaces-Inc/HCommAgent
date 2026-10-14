# Operations and recovery

This runbook describes current source controls and operator requirements. Source
updates do not prove live cloud configuration, migration privileges, restored
backups, traffic rollout or measured service limits. Main can start a build, but
`_APPROVED_COMMIT` defaults to empty and its first guard blocks cloud mutations.
An authorized operator must supply the exact tested full SHA matching `COMMIT_SHA`
for the intended build after reviewing rollout prerequisites; source publication
and client validation do not supply production approval.

## Production prerequisites

Use `HCA_ENV=production`. Startup requires Postgres, admin/sweep/pepper values of
at least 32 characters, Google client audiences, `HCA_REQUIRE_GOOGLE_LINK=1` and
secure webhook mode. Configure current permitted tenant owners before linking
devices. An explicitly empty owner list grants nobody access.

| Configuration | Purpose |
| --- | --- |
| `DATABASE_URL` | Postgres runtime principal with only required application operations |
| `HCA_ADMIN_TOKEN` | Separate platform-administration credential |
| `SWEEP_TRIGGER_TOKEN` | Separate scheduler credential |
| `HCA_API_KEY_PEPPER` | Caller-key HMAC pepper; also required to read retained legacy webhook ciphertext |
| `HCA_REQUIRE_GOOGLE_LINK=1` | Require linked current owners |
| `HCA_GOOGLE_CLIENT_ID` / `HCA_GOOGLE_CLIENT_IDS` | Accepted configured OAuth client audiences |
| `HCA_OWNER_EMAILS` / tenant owner administration | Explicit owner membership for the intended tenant |
| `_MIGRATE_SA`, `_MIGRATE_DB_URL_SECRET` | Separate migration service account and DDL principal/secret |
| Database pool and instance bounds | Fit runtime, listeners, overlapping revisions and migrations within the connection budget |

The source pipeline separates migration/runtime identities and validates an exact
candidate. Operators must actually provision the service accounts, database
principals, scoped grants and secret versions; substitutions cannot create them.
Preserve sanitized configuration/grant evidence. The [deployment/rollback runbook](runbooks/HCA_DEPLOY_ROLLBACK.md)
provides the current pipeline details; [migration/recovery](runbooks/HCA_MIGRATION_RECOVERY.md) describes bounded locks and least-privilege setup; [initial setup](runbooks/HCA_FIRST_DEPLOY.md)
is historical context requiring reconciliation.

Never enable `HCA_WEBHOOK_ALLOW_INSECURE` in production. The mock demo deliberately
uses loopback development mode. Protect admin/sweep/API/signing credentials outside
clients, source, logs and command transcripts. Assess existing records and
registrations above new bounds before rollout; rejecting old oversized data is
not a data-migration plan.

## Candidate validation, rollout and recovery

1. Pin the source/image and retain exact CI, security scan, dependency, SBOM and
   provenance evidence. After reviewing prerequisites and authorizing that rollout,
   set `_APPROVED_COMMIT` to that exact tested `COMMIT_SHA` for the intended build.
   Manual submissions require a clean checkout of that SHA; leave approval empty
   for routine source integration. Record previous serving revision and traffic.
2. Back up the database and exercise restore before schema changes. Test upgrade
   from the deployed schema and compatibility with old/new revisions overlapping.
3. Run migrations under the separate principal with bounded locks and a schema-head
   assertion. A bounded Postgres advisory lock serializes migration sessions. Automatic
   idle-session termination is off; explicit recovery requires the exact runtime DB
   role plus `application_name=hca-api` and excludes unnamed clients. Omit query contents.
4. Deploy a build-specific candidate with no production traffic. Persist its exact
   revision, URL, image and source/build identity. Check effective configuration
   and smoke that same revision; promotion must use the validated revision.
   The build-specific tag is `candidate-<first 8 hex characters of BUILD_ID>` (Cloud Run limits the combined tag and service name length; the full build id stays on the revision label) and the shared snapshot is
   `/workspace/hca-candidate.json`. Its source/build/image/revision and config/smoke
   evidence must agree. Migration jobs also use a build-specific identity.
5. Verify actual owner authorization, signed receiver deduplication, stream reconnect
   and revocation on the authorized deployment. The owner-approved client validation
   is [recorded separately](reviews/2026-10-12_manual_validation_approval.md); this
   repository does not invent additional live deployment proof.
6. Promote only the validated candidate under the pipeline's concurrency controls.
   Promotion uses the captured service etag and exact revision; a concurrent service
   change fails closed instead of automatically retrying promotion. Record identities
   and rollback target. The automated live smoke covers the agent
   create/read/cancel path; it is not a real Google-linked human approval proof.
7. If authorization, integrity or delivery degrades, move traffic to the previously
   verified compatible revision. Traffic rollback does not undo a schema change or
   make an old binary able to decrypt new ciphertext.

Real grants, restored backups, revision promotion/rollback and load evidence remain
operator responsibilities even when pipeline tests pass.

## Credentials and rotation

For caller-key compromise, use `POST /v1/api-keys/{prefix}/rotate` for an administrator or `POST /v1/api-keys/self/rotate` from the automation. Store the replacement secret securely, switch traffic, and verify lifecycle events with `GET /v1/api-keys` and `GET /v1/api-keys/{prefix}/events`. The old key remains valid only through `previous_valid_until`; revoke immediately when overlap is not acceptable. Investigate the key’s requests and receivers. See [Caller API keys](api-keys.md). For device compromise, revoke the server device,
remove owner membership where appropriate, rotate/register the local key and link
an authorized current owner. Chrome local rotation discards scoped offline answers
but does not revoke the old server registration. Mac Keychain removal changes device
identity and requires registration/linking again.

For admin/sweep compromise, replace the affected secret/configuration, roll compatible
instances, verify the old bearer is refused and investigate issuance, owner/device
changes, exports/purges and sweep activity. A compromised administrator/database can
change trusted state; client signatures do not restore that trust.

Every agent key carries two ceilings, `rate_per_minute` (every authenticated call,
default 600) and `asks_per_minute` (POST /v1/requests, default 60), set at issuance or
by `PATCH /v1/api-keys/{prefix}`; a null returns the key to the deployment default
(`HCA_KEY_RATE_PER_MINUTE`, `HCA_KEY_ASKS_PER_MINUTE`). Rotation overlap is
controlled by `HCA_KEY_ROTATION_OVERLAP_HOURS` (default 24). `PATCH /v1/tenants/{tenant_id}`
with `asks_per_day` caps a whole tenant per UTC day (0 means unlimited). Counting is one
shared database upsert per call, so every instance sees the same count; a 429 carries
`Retry-After` to the window end and successful calls carry `X-RateLimit-Limit` and
`X-RateLimit-Remaining`. The first refusal in a window writes one `limit_hit` event
(`GET /v1/api-keys/{prefix}/events`) and `/v1/internal/metrics` reports
`api_keys.rate_limited_last_hour`; a key that keeps hitting its ceiling is either an
automation loop or a leaked key, so read its events before raising the ceiling.

Receiver signing-secret rotation uses `POST /v1/webhooks/{id}/rotate` with a 24-hour
old/new signing overlap. The receiver must install the new secret and continue
accepting verified overlap signatures. A second rotation returns 409 while the
previous overlap remains, and is permitted at its boundary. Revocation stops new
attempts after their initial registry check; it cannot undo an HTTP request already
in flight or an external effect. Signing-secret rotation and encryption rewrap are
separate operations.

### Independent encryption-key transition

`HCA_WEBHOOK_ENCRYPTION_KEYS` is an optional JSON keyring, at most ten key IDs mapping
to URL-safe base64-encoded 32-byte keys. `HCA_WEBHOOK_CURRENT_KEY_ID` selects a present
key. With no ring, legacy pepper-derived ciphertext remains supported. Enabling the
ring writes versioned independent ciphertext; retained old ciphertext is read with
the current pepper or explicit previous peppers.

Deploy dual-read source to every instance and drain incompatible revisions before
enabling versioned writes. Retain the old pepper and all required decrypt keys.
Provision the ring/current ID through secure deployment configuration, then run
these commands in an authorized environment with an explicit `DATABASE_URL`:

```sh
python scripts/rotate_webhook_encryption.py
python scripts/rotate_webhook_encryption.py --apply --batch-size 200
python scripts/rotate_webhook_encryption.py
```

The first/final calls are dry runs. Preflight authenticates every retained current
and previous ciphertext, including revoked destinations and expired overlap rows,
before apply begins. Apply rechecks locked rows and commits bounded batches; earlier
batches may remain committed after a later failure, so retain the reported batch
count and rerun safely after correcting the cause. Output contains counts, mode and
key ID, without signing secrets, ciphertext or customer/destination identifiers.
Rewrap does not rotate the receiver's signing secret. The final verification must
show no ciphertext needing rewrap before retiring old material; backups that still
require old keys need their own migration/expiry path.

`HCA_API_KEY_PREVIOUS_PEPPERS` is an optional JSON list of at most three prior peppers.
Successful verification under an overlap pepper upgrades that caller hash using a
conditional update; dormant keys still need use or replacement before retirement.
An overlap does not invalidate a stolen caller secret. Revoke compromised keys
explicitly. Do not remove an old pepper while any legacy ciphertext, backup or
unmigrated caller credential still depends on it.

## Retention, export and deletion

Bounded cleanup retains events/completed jobs for seven days, terminal webhook
attempts/deliveries for thirty days and registry events for ninety days. Active
queued deliveries and unfinished jobs are preserved. Replay is available for a
bounded twenty-four-hour window; replay availability differs from physical retention.
Expired retry/nonce/rate buckets are cleaned. Decision audits require explicit purge.

`GET /v1/tenants/{tenant_id}/records` is an admin-protected decision export, default
200/max 500 per page. Follow `X-HCA-Next-Cursor` using `after`. The purge route
`POST /v1/tenants/{tenant_id}/records/purge` requires the tenant slug, a past,
timezone-aware cutoff and bounded batch limit. It removes eligible terminal asks
and associated decision/audit/event/retry/delivery records. Pending requests,
queued deliveries and live leases are preserved and rechecked under locks.
Export the intended records before applying a retention decision; deletion removes
dispute/recovery evidence. Resolve active/held asks before deleting their records.

Owner/device profiles, destination registrations, external receiver ledgers,
backups and infrastructure logs need an operator-defined handling policy. Running
DB deletion does not erase backups. Supply actual operator contact and restore-safe
retention windows for the deployed service. [The served privacy page](../api/app/privacy.py)
describes these boundaries without promising unsupported universal deletion.

Chrome offline responses are capped at 100 entries, 32 KiB per answer, 1 MiB total,
20 attempts and twenty-four hours or an earlier non-hold deadline. Origin changes,
key rotation and authorization refusal clear sensitive scoped state. Mac and system
notification histories require their documented local cleanup. Notification previews
are hidden by default and may be enabled by a user.

## Monitoring and incidents

Monitor redacted logs/internal metrics for worker/listener failures, replay gaps,
subscriber drops, expired leases, authentication/rate failures, delivery queue age
and dead deliveries. Keep correlation/request/delivery IDs without logging credentials,
full sensitive answers or tokens. The code exposes observability; it does not install
an alerting service or establish SLO, throughput or cost numbers.

The default retry policy makes eight attempts: the initial delivery plus seven
waits totaling 6,156 seconds (102 minutes 36 seconds) before delivery time and queue
latency. Jitter can increase configured waits by twenty percent. Custom schedules
are bounded; a dead delivery, including exhausted internal work, needs investigation.
Do not recreate effects blindly: reconcile authenticated receiver acknowledgements
and deduplication ledgers before an authorized redrive/recovery.

Set/test actual alert thresholds, edge traffic limits, global stream/instance caps,
storage and connection budgets against a measured workload. Per-process device
stream limits are not a global cross-instance quota. For incidents, restrict the
identity/destination, preserve sanitized evidence, identify the last trusted
revision/state, repair configuration/source and follow deduplication-aware recovery.
Report product vulnerabilities through [private reporting](../SECURITY.md).

## Evidence and distribution limits

Retain candidate/previous revisions, exact CI/scan results, schema upgrade, privilege
and configuration evidence, approved real-owner/receiver checks, traffic/rollback,
restored backups and load/soak limits. [Current status](STATUS.md) distinguishes
recorded owner validation from missing infrastructure evidence and outstanding alerts.

Public notarized Mac distribution is outside the developer-only scope. It would need
Developer ID, hardened runtime/entitlements, notarization and verified install/update
behavior. Removing personal operations from product source does not create a private
operations repository or erase public Git history. No formal compliance claim is made.
