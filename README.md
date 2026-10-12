# Human Collections Agent

HCA lets an agent pause for a human decision, present its context, record an authorized response and resume through a signed webhook or polling. It includes a FastAPI service, a Chrome decision inbox and a developer macOS menu-bar client.

The supported authorization model is **current tenant owners**. Caller keys belong to a tenant and caller; production human devices must be registered and linked to a permitted Google account. The `audience` field describes a request and does not grant department or role permissions.

Start with the synthetic demonstration and [case study](docs/CASE_STUDY.md), then read [current status](docs/STATUS.md) for implemented behavior, exact evidence and remaining deployment requirements. Source changes and green checks do not establish production rollout or formal compliance certification.

## Run the demonstration

Use Python 3.12 from the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r api/requirements-dev.lock
python scripts/demo_workflow.py
```

The demo starts a disposable loopback API, database and mock receiver. It creates a synthetic request, submits a device-signed decision, verifies the webhook independently, commits one mock effect with its receiver deduplication record, checks the audit, and retries the decision and delivery. It prints a concise PASS report and cleans up. It requires no personal account, cloud project or existing credentials. Its simulated human signer exercises the API contract; it does not replace real Google sign-in or native client validation.

To generate a separate local development environment and inspect the API:

```sh
python scripts/dev_setup.py
python scripts/dev_setup.py --serve
```

Setup generates disposable credentials in private, ignored `.env.development`, migrates `api/hca_development.db`, and binds only to `127.0.0.1:8000` when serving. It refuses production configuration, an unrelated database path and accidental configuration overwrite. Open `http://127.0.0.1:8000/docs`. Stop with Control-C. See [Contributing](CONTRIBUTING.md) for Postgres checks and safe cleanup. The Chrome client requires HTTPS even for development; the script demonstration supplies its own signed client.

## Supported clients

| Control | Chrome 122+ | macOS 14+ developer client |
| --- | --- | --- |
| Radio | Yes | Yes |
| Approve/deny | Yes, with full context review | Yes, with full context review |
| Checkbox | Yes | Use Chrome |
| Searchable dropdown | Yes | Use Chrome |
| Chat | Yes, bounded text | Use Chrome |
| Row confirmation | Yes | Use Chrome |

Suggestions never preselect responses. Material approvals expose the resource, change, impact, exact command and rollback before explicit confirmation. Both clients hide notification details by default. Chrome persists non-extractable keys and origin/device-scoped responses; Mac uses Keychain and authorized notification recovery. Read the [Chrome](extension/README.md) and [Mac](macos/README.md) instructions for registration, Google linking, upgrades and cleanup.

The Mac release path is developer-only. Public Developer ID signing, notarization and an automatic updater are outside the supported distribution scope. See [release instructions](docs/RELEASES.md) for candidate version 0.3.0, compatible artifacts, numeric build numbers and checksums. Publishing source does not update installed clients.

## Decision and delivery guarantees

1. Protected calls use current tenant, owner and device state. Streams recheck authorization on a clock, including while events arrive continuously.
2. Deadline and terminal-state checks occur under a database lock. Non-hold decisions cannot bypass expiry because a worker is late; `hold` intentionally stays answerable.
3. Resource-bound idempotency, business state, audit, durable event and webhook outbox commit together. Concurrent retries cannot produce a second committed decision.
4. Workers claim near execution, renew leases and fence stale writes. External delivery remains at least once: receivers authenticate the signed delivery ID and deduplicate their effects transactionally.
5. Bounded SSE replay signals a reset when recovery is necessary. Clients reconcile the paginated inbox and poll after suspension or disconnects.

API bodies, nested data, reads, registrations, active asks and request budgets have explicit bounds. Export/purge and transient cleanup preserve pending decisions, queued deliveries and active leases. [Architecture](docs/ARCHITECTURE.md), [Threat model](docs/THREAT_MODEL.md) and [webhook receiver guidance](docs/webhooks.md) describe the boundaries and limitations.

## Repository map

| Path | Purpose |
| --- | --- |
| `api/` | API, schemas, migrations, dependency inputs/locks and regression tests |
| `extension/` | Chrome surface, signed calls, scoped queue/state and Node/Chromium checks |
| `macos/` | Swift core, menu-bar client, TCP OAuth tests and developer bundle tooling |
| `scripts/` | Local setup/demo, smoke checks and release packaging |
| `.github/` | Quality gates, pinned workflow actions, updates and code ownership |
| `docs/` | Current guidance, evidence and explicitly dated historical records |
| `cloudbuild.yaml` | Build, migration and staged candidate validation/promotion |

Personal privileged operations are outside the product scope. [Current status](docs/STATUS.md) records source-isolation and historical-review boundaries; removing files from the product head does not erase Git history.

## Validation and operation

The supported development tools are Python 3.12, Node 22 or 24 (CI: 24) and Swift 5.9 or later on macOS 14+. CI covers Postgres migrations/tests, Node/Chromium, strict Swift, lint, dependency audit, complete API contract parity and security gates. Exact results belong to the reviewed commit. Scan results, outstanding alerts and repository settings are described separately in [status](docs/STATUS.md).

The pipeline validates a candidate before traffic promotion. Production needs verified credentials, current permitted owners, separate migration privileges and compatible schema changes. Real IAM/grants, key migration, restored backups, rollout/rollback and load limits require operator evidence. See [Operations](docs/OPERATIONS.md) and the [deployment runbook](docs/runbooks/HCA_DEPLOY_ROLLBACK.md) before merging because main can trigger deployment.

Use [Security](SECURITY.md) for private vulnerability reports and supported updates, [Contributing](CONTRIBUTING.md) for reproducible checks, and [the documentation index](docs/INDEX.md) for deeper references. Licensed under [Apache-2.0](LICENSE), with [separate dependency notices](THIRD_PARTY_NOTICES.md).
