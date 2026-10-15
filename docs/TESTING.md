---
title: Test infrastructure matrix
summary: Which HCA test suites run where, what each needs, and how CI runs them.
---

# Test infrastructure matrix

Every suite below runs in CI on each pull request (workflow `.github/workflows/gates.yml`) and on the merge build (`cloudbuild.yaml`). The table says what a suite proves and what it needs, so a contributor knows which results they can reproduce on a laptop and which need Postgres, macOS or a browser. Counts are as of main after the client safety review (October 2026) and will drift; the commands are the source of truth.

| Suite | Command | Needs | Runs in | Proves |
| --- | --- | --- | --- | --- |
| API unit and HTTP tests (about 250 tests in 38 modules) | `cd api && pytest` | Python 3.12, a disposable SQLite file (created automatically) | CI job API Tests (Postgres), Cloud Build step pytest | Routes, auth, device signatures, state machine, idempotency, webhooks, outbox, retention, resource limits, OpenAPI parity, 85 percent coverage floor |
| Postgres only proofs (4 tests) | `DATABASE_URL=postgresql+psycopg2://hca:hca@localhost:5432/hca_test pytest` | Local PostgreSQL 16 with a database named `test_*` or `*_test` | CI job API Tests (Postgres) | LISTEN/NOTIFY fan out, SELECT FOR UPDATE races, commit order advisory lock, tenant and event lock compatibility; skipped on SQLite with a reason |
| Two process tests (`tests/test_two_process.py`) | part of `pytest` | Free local ports; uses the same database as the run | Same | A second uvicorn process sees the first process's commits and leases through the database, not shared memory |
| Durability regression matrix (`tests/test_durability_regressions.py`, 20 tests) | part of `pytest` | None beyond the suite | Same | Process failure, concurrent retries, owner removal, busy stream revocation, future timestamp replay, overdue decisions, lease takeover, slow recipients, signed dedupe ids |
| Golden path smoke | `python3 scripts/smoke_golden_path.py` against a running API (local, candidate and live variants in `cloudbuild.yaml`) | A running API; the candidate run uses a no traffic Cloud Run revision | Cloud Build steps smoke-local, smoke-candidate, smoke-live | Mint key, post an ask, answer with a test device, audit and webhook rows, key revoked at the end |
| Extension unit tests (Node 24) | `cd extension && npm test` | Node 22 or 24 (CI: 24), no browser | CI job Extension Tests (Node 24), Cloud Build step pack-extension | Signature canon, SSE parsing, offline queue rules, origin validation, controls, packaging |
| Browser proofs (real Chromium) | `cd extension && npm ci && npx playwright install chromium && npm run test:browser` (runner `tests/browser/run.js`; set `HCA_CHROMIUM_PATH` to use an installed Chrome) | Node 24, Playwright 1.64.0 (pinned in package-lock), Chromium headless shell download | CI job Extension Tests (Node 24), after the unit tests, six minute step budget | Nine named PASS scenarios: offlineAnswer, reconnect, workerRestart, controlsAndDuplicateSubmit (the six control run plus IndexedDB key initialization), concurrentQueueWrites, notifications, permissionDenial, malformedEvents, mobileFirstLayout. The real background.js module runs inside Chromium over a recorded chrome mock with network routed by Playwright; a worker restart is a second createBackground over the same storage |
| Swift tests (macOS) | `cd macos && swift test` | macOS 14 or later (Package.swift platform), Xcode with Swift 6 strict concurrency, CI uses macos-latest | CI job Swift Tests (macOS) | Device signer vectors, SSE parser, inbox and cursor logic, OAuth loopback listener over a real local TCP socket, single flight, notification shape and recovery |
| Repository gates | `python3 .github/gates/check_no_dashes.py`, `check_client_data.py`, `check_manifest.py`, `check_openapi_parity.py` | Python 3.12, PyYAML for the manifest gate | Four CI jobs plus the Cloud Build guard step | No em or en dashes, no client data, bootstrap manifest integrity, OpenAPI yaml equals the generated schema |
| Lint and dependency audit | `ruff check api .github/gates scripts`, `pip-audit -r api/requirements-dev.txt` | Python 3.12 | CI jobs Python Lint (ruff), Python Dependency Audit (pip-audit) | Style profile in `ruff.toml`, no known vulnerable pins |

## Database isolation (review item 27)

`api/tests/conftest.py` decides the database before any application module is imported:

- With no `DATABASE_URL` or `HCA_TEST_DATABASE_URL` set, each run gets a fresh SQLite file in a temporary directory that is deleted at the end of the session.
- A PostgreSQL URL is accepted only for `localhost`, `127.0.0.1`, `::1` or the host `postgres`, and only when the database name starts with `test_` or ends with `_test`. Anything else aborts the run before a single table is touched.
- A SQLite URL is accepted only in a temporary directory or with `test` in the file name.
- Each test module starts from an empty schema with the default tenant, stopped workers, a reset replay guard and zeroed counters; the module's fixtures then build their own records. No module performs DDL at import time.

Test modules therefore no longer set `DATABASE_URL` themselves; the former per module `os.environ.setdefault` lines were dead after the conftest change and have been removed.

## What the automated suites do not establish

Real Google owner OAuth, unpacked Chrome permission and alarm scheduling, native macOS notification and Keychain lifecycle, assistive technology accessibility, and production load or recovery behaviour. Those need a configured test account and the operator's environment; the deploy records under `docs/deploys/` hold the human proofs for each phase.
