# Human Collections Agent

HCA lets an agent pause at a human decision, present the relevant context, record the authorized response, and resume through an authenticated webhook or a poll. It includes a FastAPI service, a Chrome decision inbox, and a developer macOS menu bar client.

The supported authorization model is **tenant owners**. An agent key belongs to one caller and tenant; a human device must be registered and linked to a current permitted Google account in production. The `audience` field describes a request. It does not implement arbitrary departments, role-based routing, or a separate permission boundary.

For a hiring review, start with the credential-free demo and [case study](docs/PORTFOLIO.md). The [remediation record](docs/SECURITY_REMEDIATION.md) maps the security review to the code and the evidence still needed. This source update has not been deployed to a production service; a repository cannot guarantee a hiring outcome or establish compliance certification.

## Run the five-minute demonstration

Use Python 3.12 from the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r api/requirements-dev.lock
python scripts/dev_setup.py
python scripts/demo_workflow.py
```

Setup generates disposable local credentials and an explicit development configuration. The demo starts a loopback API and mock webhook receiver, creates a synthetic request, submits a device-signed decision, verifies the signed delivery, records the mock effect and audit, and retries the same operation to check that a second effect is not produced. It requires no personal Google account, cloud project, or author's credentials. It exercises a scripted human-client contract; it does not simulate production Google authentication or native notification presentation.

To leave the local API running for inspection:

```sh
cd api
../.venv/bin/uvicorn app.main:app --env-file ../.env --reload
```

Open `http://127.0.0.1:8000/docs`. Development uses a generated SQLite path; production requires Postgres. Do not point the tests or local demo at an application database. See [Contributing](CONTRIBUTING.md) for Postgres tests and [Operations](docs/OPERATIONS.md) for production requirements.

## Supported clients and controls

| Control | Chrome 122+ | macOS 14+ developer client |
| --- | --- | --- |
| Radio | Yes | Yes |
| Approve/deny | Yes, with complete material context | Yes, with context review |
| Checkbox | Yes | Use Chrome |
| Searchable dropdown | Yes | Use Chrome |
| Chat | Yes, bounded text | Use Chrome |
| Row confirmation | Yes | Use Chrome |

Chrome uses non-extractable device keys in IndexedDB, an origin-scoped inbox, a bounded durable response queue, and fresh signatures on each retry. Suggestions do not preselect answers. Both clients hide decision details in notifications by default. Material approvals expose the resource, change, impact, command and rollback before an explicit decision. Read the [Chrome instructions](extension/README.md) and [macOS instructions](macos/README.md) for registration, Google linking, upgrades and local cleanup.

The macOS build is developer-only. Local or ad-hoc signing is available; public Developer ID signing, notarization and an automatic updater are outside the advertised scope. Chrome packaging produces a versioned archive and checksum; an operator must verify its extension ID and OAuth client configuration before distribution.

## What the service guarantees

1. Caller and tenant authorization comes from verified server state. Removing an owner prevents an already-linked device from continuing to use protected routes; streams recheck current authorization on a clock.
2. Decisions and expiry are checked under a database lock. A non-hold decision cannot be accepted after its deadline just because a worker has not run yet. `hold` deliberately remains answerable.
3. An idempotency reservation, the business change, its audit, event and webhook outbox share a transaction. The fingerprint includes the requested resource, so a key cannot replay another request's answer.
4. Leased workers renew their claim and fence stale writes. External deliveries have at-least-once semantics: a receiver must authenticate the signed delivery ID and deduplicate its effect transactionally.
5. SSE replay is bounded. A replay gap triggers an explicit reset and inbox reconciliation; clients also poll to recover from suspension and disconnects.

These properties have regression tests. Database-specific ordering and multi-process behavior need the Postgres checks; browser, OAuth, platform and deployment evidence have their own limits. Read [Architecture](docs/ARCHITECTURE.md), [Threat model](docs/THREAT_MODEL.md), and the [webhook receiver contract](docs/webhooks.md) before relying on the service as an approval boundary.

## Repository map

| Path | Purpose |
| --- | --- |
| `api/` | API, schemas, database models, migrations, runtime and development dependency locks, tests |
| `extension/` | Chrome surface, queue/key/state helpers, Node and Chromium checks |
| `macos/` | Swift core, menu bar client, loopback OAuth tests, developer bundle tooling |
| `scripts/` | Local setup/demo, smoke checks, packaging and candidate configuration checks |
| `.github/` | Quality gates, pinned Actions, dependency updates, code ownership |
| `docs/` | Current architecture, security/operations guidance, portfolio evidence and historical records |
| `cloudbuild.yaml` | Build, migration, no-traffic candidate validation, smoke and promotion |

Privileged personal-agent operations have been removed from the product tree. Historical plans and deployment notes describe their date and source revision; they are not current operating instructions or proof that this update is deployed.

## Validation and deployment

CI runs Python 3.12 API tests against a disposable Postgres database, Node 24 extension and Chromium tests, Swift tests on macOS, lint, dependency audit, credential/history scanning, the operations-isolation gate, complete generated OpenAPI parity, and container scan/SBOM checks. CodeQL coverage and repository enforcement are tracked separately in the [dated settings evidence](docs/REPOSITORY_SETTINGS_2026-10-12.json). Exact results belong to the reviewed commit's CI, not a permanent test count in this README.

The release image uses locked runtime dependencies, a digest-pinned base, a non-root user and process signal forwarding. The deployment pipeline requires explicit production authentication and separate migration identity, deploys a candidate with no production traffic, checks configuration and runs the candidate smoke before promotion. Its automated live smoke covers an agent request/cancel flow. Real Google-linked human approval, cloud IAM, rollback/restore and live load evidence remain separate operator checks. See [Operations](docs/OPERATIONS.md).

Use [Security](SECURITY.md) for private vulnerability reporting and the supported update policy, [Contributing](CONTRIBUTING.md) for reproducible checks, and the [documentation index](docs/INDEX.md) for the current entry points. HCA is licensed under [Apache-2.0](LICENSE).
