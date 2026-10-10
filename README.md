# Human Collections Agent (HCA)

HCA is a human control plane for agentic retail, marketing, merchandising and creative operations. AI agents post the questions only a person may answer (approve this spend, pick one of these four layouts, confirm these rows) through one API; HCA puts each question in front of the responsible human as a UI selection on the best available surface, records the decision with who answered, where and when, and delivers the answer back to the agent. Every open question is counted as N of M against active requests, never narrated.

Who it is for: teams running autonomous agents that must stop at a human gate without stopping the agent, and engineers who need that gate to be auditable, multi-tenant and hard to bypass.

## Surfaces

The same ask reaches the human on a ladder of surfaces, all on the same API and the same answer record:

- **Chrome extension** (`extension/`): popup inbox, server-sent events plus polling, device-signed answers, options page for the API base URL and surface priority.
- **macOS menu bar app, HCA Notifier** (`macos/`): native notification banners with action buttons, menu rows for pending asks, launch at login, liveness indicator.

The server decides who sees an ask first. Each device carries a priority; eligible devices seen within the active window are ranked, and an ask becomes visible to rank *r* at `created_at + r * window` (90 seconds by default, 45 seconds for red gates), so a banner the owner ignores on the Mac shows up in Chrome a window later (`api/app/routing.py`).

## How it works

1. An agent calls `POST /v1/requests` with an API key: a title, why it matters, a control (`approve_deny`, `radio`, `dropdown`, `checkbox`, `chat`, `confirm_rows`), its options, a deadline and an expiry policy. Values are validated against the control catalogue in `api/app/controls.py`; an `Idempotency-Key` header makes retries safe.
2. The ask is stored under the caller's tenant and a `request.created` event is published to the tenant's stream; escalation timers are scheduled for each surface rank.
3. Human clients read `GET /v1/inbox` and `GET /v1/inbox/stream` with a device signature; the server filters both to the asks that device may see right now.
4. The human answers on the surface that reached them; the client posts `POST /v1/requests/{id}/answer`, signed by the device key.
5. The server locks the ask row (`SELECT ... FOR UPDATE`), accepts exactly one terminal transition, validates the answer value against the control, and writes the audit row: device, surface, path taken, value, timestamps.
6. A `request.answered` event goes to every open stream so other surfaces drop the card; the agent's webhook is called with retries, each attempt recorded.
7. Asks nobody answers are swept at their deadline per the ask's expiry policy: auto denied (the default), expired, or held (`api/app/expiry.py`).
8. `GET /v1/audit` returns the answer records with filters by agent, window, surface and outcome.

## Repository layout

| Path | What lives there | Tests |
| --- | --- | --- |
| `api/` | FastAPI service, SQLAlchemy models, Alembic migrations, the OpenAPI contract (`api/openapi.yaml`) | `cd api && pytest -q` |
| `extension/` | Chrome extension (Manifest V3), device key in the extension, options page | `cd extension && npm test` |
| `macos/` | SwiftPM package: `HCACore` library and `HCANotifier` menu bar app | `cd macos && swift test` |
| `scripts/` | Smoke test against a live deployment, extension packaging, store helpers | run by Cloud Build |
| `.github/` | CI gates, workflows, templates, Dependabot, CODEOWNERS | run on every PR |
| `cloudbuild.yaml` | Build, test, migrate, deploy and smoke pipeline | |
| `docs/` | Charter, plans per phase, runbooks, deploy records, decisions, reviews | |
| `bootstrap/` | Seat tooling for the agent that maintains this repo (moving to a separate operations repo, decision 3 in the P19 program plan) | manifest gate |

## Quickstart, local

Run in a terminal from the repository root. Python 3.12 and Node 20 are what CI uses.

```
cd api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
pytest -q
uvicorn app.main:app --reload
```

Without `DATABASE_URL` the service uses a local SQLite file; CI and production run Postgres. The test suite at the time of writing: 105 passed, 1 skipped. Interactive API docs are served by FastAPI at `/docs` once the server is running; the contract of record is `api/openapi.yaml`, and a CI gate fails any PR where the two disagree.

## Security model

- **Agents** authenticate with an API key (`Authorization: Bearer hca_live_...`), one live key per caller, stored as a SHA-256 hash of a 192-bit random secret, scoped to a tenant (`api/app/apikeys.py`, `api/app/auth.py`).
- **Humans** authenticate as devices: a P-256 key generated on the device, registered once by an administrator, then every call to a human route carries `X-HCA-Device`, `X-HCA-Timestamp`, `X-HCA-Nonce` and `X-HCA-Signature` over method, path, body hash and timestamp; the server checks freshness and signature (`api/app/devicesig.py`, `api/app/auth.py`). Shared test vectors keep the Swift and JavaScript signers in step with the Python verifier.
- **Owner gate**: a device must link a Google account that is on the tenant's owner allowlist before it can read or answer asks (`api/app/googleauth.py`, `api/app/tenancy.py`).
- **Tenancy**: every ask, device, key, audit row, webhook attempt and event carries a `tenant_id`; reads, streams and writes are filtered on it from authenticated server state, never from the client.
- **Platform administration** (tenants, owners, devices, keys) uses a separate admin bearer; the expiry sweep uses its own bearer.
- **Concurrency**: row locks and a single terminal transition per ask; `Idempotency-Key` replay for agent writes.

Vulnerability reports: see [SECURITY.md](SECURITY.md).

## Deployment

Cloud Build runs `cloudbuild.yaml` on every merge to main: `guard` (gates), `pytest`, `smoke-local`, `pack-extension`, `build`, `push`, `migrate` (an Alembic job that then asserts the database is at head with every model table present), `deploy` (Cloud Run, new revision at 100 percent), `smoke-live` (end to end against the new revision: mint a key, post an ask, answer with a test device, check audit and webhook, revoke the key). The first-time setup is in [docs/runbooks/HCA_FIRST_DEPLOY.md](docs/runbooks/HCA_FIRST_DEPLOY.md). Project ids, hostnames and secrets are runbook and Secret Manager values; none live in this repository.

## Quality gates (CI, every PR)

Check No Dash Characters, Check Client Data Hygiene, Check Bootstrap Manifest Integrity, Validate OpenAPI Specification (including parity between the yaml and the running app), API Tests (Postgres, with an 85 percent coverage floor), Extension Tests (Node 20), Swift Tests (macOS), Python Lint (ruff), Python Dependency Audit (pip-audit), CodeQL (Python, JavaScript, Swift, Actions). How to fix a red gate: [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, data model, state machine, routing, diagrams.
- [docs/HCA_CHARTER_v1.md](docs/HCA_CHARTER_v1.md): what HCA is and is not.
- [docs/plans/](docs/plans/): one plan per phase; the current program is [HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md](docs/plans/HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md).
- [docs/deploys/](docs/deploys/): dated deploy and proof records (what went live, which build, what was verified).
- [docs/decisions/](docs/decisions/): decisions of record with the alternatives declined.
- [docs/runbooks/](docs/runbooks/): first deploy, Chrome Web Store.
- [docs/INDEX.md](docs/INDEX.md): every file, one line each.
- [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Status

Live and proven in production: the API with surface routing, device authentication, Google owner gate, Chrome extension (published unlisted), macOS Notifier with launch at login, Cloud Build pipeline with migrate assertion and live smoke, and P19 (tenancy, strict controls, one terminal transition, idempotency, OpenAPI parity gate). Open: P20 to P27 of the hardening program, listed in the plan file above.

## License

Apache License 2.0, see [LICENSE](LICENSE).
