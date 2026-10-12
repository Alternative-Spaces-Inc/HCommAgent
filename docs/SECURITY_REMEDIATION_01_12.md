# Security remediation: review items 1 to 12

Scope: security and approval correctness changes against the public repository at `0f78e630e99444f85d666409aa4d6f4537e88331`. Client, packaging, dependency, data lifecycle, deployment and portfolio work is coordinated separately. This document records the focused implementation and its limits.

| Item | Result | Evidence |
| --- | --- | --- |
| 1 | Idempotency fingerprints include method and resource path; key reuse for another request returns 422. | answer/cancel wrong-resource regressions |
| 2 | Reserve the unique key before effects; receipt, ask, audit and outbox commit together. Concurrent create retries share one receipt. | simultaneous retries, precommit failure and lost response tests |
| 3 | State changes append events in the same transaction. All PostgreSQL event writers hold an advisory transaction lock before sequence allocation, preventing a later commit cursor from skipping an earlier event. | create/answer/cancel/expiry rollback and PostgreSQL commit ordering tests |
| 4 | Owner removal revokes matching devices. Protected routes check current membership and tenant status. Answer and self-priority writes refresh authorization inside their transaction. | removed-owner and stale accepted request regressions |
| 5 | Explicitly emptied owner lists stay empty; empty allowlists refuse linking. Production requires PostgreSQL, strong required secrets, Google clients, the owner gate and secure webhook mode. Managed service startup cannot select development mode. | production configuration and fallback regressions |
| 6 | Streams check current membership on an elapsed clock, before replay and after a wait reaching the deadline. Default check interval is 15 seconds. | busy, quiet, late event and delayed replay regressions |
| 7 | Both nonce stores retain records for 601 seconds, covering future timestamps accepted with 300-second skew. Header lengths are bounded. | future timestamp/pruning and shared database replay tests |
| 8 | A locked answer checks its deadline and applies expiry without waiting for a worker. Hold requests remain answerable after their deadline. | exact deadline, overdue answer and hold tests |
| 9 | Jobs claim just before execution, renew leases and fence effects/completion by owner and generation. Stale workers cannot release newer leases. | takeover, long job and queued batch regressions |
| 10 | Webhooks have one cancellable overall deadline including bounded DNS work. Database connections are released during network IO. Only status acknowledges delivery; response bodies are not consumed or decompressed. | hanging DNS, slow headers/body, compression and connection release tests |
| 11 | The signed body includes the stable delivery ID; verifier and smoke receiver require header agreement. Receiver guidance includes an atomic durable deduplication example. | substituted ID and malformed signature tests |
| 12 | Audit identity, role and surface come from authenticated state; verified subject/email/device are snapshotted. Client identity claims are isolated under client_metadata. | impersonation regression |

## Authentication and audit semantics

Production means `HCA_ENV=production`, or a managed Cloud Run service identified by `K_SERVICE`. Local development defaults to development mode; it can explicitly set `HCA_ENV=development` with a disposable SQLite database. The required production admin token, sweep token and API-key pepper each have at least 32 characters. Configure Google OAuth client IDs and `HCA_REQUIRE_GOOGLE_LINK=1` before rollout. An owner must exist in the tenant allowlist; the deployment owner fallback applies only to a never-configured default tenant.

Revocation prevents fresh protected requests and changes committed before a write's current authorization check. It does not undo a decision already accepted. Stream checks have a 15-second interval; scheduler delay and database response time can add latency, so this is not a hard real-time SLA.

`audience`, `path_hint` and client-provided `path_taken`, `duration_ms` and `voice_used` are descriptive metadata, not proof of a role or navigation path. The current policy grants an authorized tenant owner access to that tenant's approval requests. Role-specific approval policy is not claimed. Client telemetry remains descriptive even when presented at the top level of an answer record.

Expiry policy is explicit: `auto_deny` records a denial, `expire` expires without a human answer, and `hold` remains pending and answerable. A red gate does not override an explicitly configured policy. There is no claim that red gates always automatically deny.

## Validation and deployment limits

The regression suite uses isolated synthetic test databases and controlled outbound network responses. Failure injection proves transaction rollback and retry behavior; it is not a production process-kill or load test. PostgreSQL-only tests cover database lock/sequence behavior in CI. Local SQLite success does not substitute for those checks.

Deploy only after reviewing the final branch, verifying PostgreSQL CI and migrations, confirming production configuration, and rehearsing recovery. This branch has not been merged or deployed. The existing pepper-based webhook encryption format remains pending a separate migration. See [Webhook receiver contract](webhooks.md) for at-least-once delivery and durable deduplication.

## Owner validation approval

The owner reported manual validation completed and approved on October 12, 2026. See the [approval record](reviews/2026-10-12_manual_validation_approval.md). Deployment code and configuration limits remain as documented.
