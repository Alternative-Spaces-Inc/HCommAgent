# Contributing

Use a branch and a pull request. Describe the concrete behavior change, failure case, checks and remaining limits. Review the exact candidate and its CI before merging. Main can trigger deployment; source review and cloud authorization are separate.

## Reproducible development

Use Python 3.12, Node 22 or 24 and macOS 14+ with Swift 5.9 or later. From the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r api/requirements-dev.lock.txt
python scripts/demo_workflow.py
```

Runtime dependencies are in `api/requirements.txt` and `api/requirements.lock.txt`; development inputs and their compiled dependencies are in `api/requirements-dev.in` and `api/requirements-dev.lock.txt`. Update source inputs and regenerate both hashed locks together. Use `npm ci` with `extension/package-lock.json`. Changes to locks, action pins or image digests need review and the relevant checks.

The `.lock.txt` suffix allows Dependabot to discover the compiled files and the nested include in `requirements-dev.txt`. Keep that compatibility file as a wrapper. Dependency update proposals still require reviewed regeneration of both hash locks and the recorded direct-input digests; the updater does not maintain this custom dual-lock workflow automatically.

The current locks were generated with uv 0.11.14 for Python 3.12 across supported platforms. After changing direct pins, extras or markers:

```sh
uv pip compile api/requirements.txt --universal --python-version 3.12 --generate-hashes --output-file api/requirements.lock.txt
uv pip compile api/requirements-dev.in --universal --python-version 3.12 --generate-hashes --output-file api/requirements-dev.lock.txt
python .github/gates/update_dependency_inputs.py
python .github/gates/check_dependency_locks.py
```

Review both resolved locks and the input digest change together. CI installs with `--require-hashes` and rejects input drift, including unchanged-version extra or marker edits.

For a persistent disposable local API, `python scripts/dev_setup.py` creates `.env.development` with mode 0600 and migrates `api/hca_development.db`. `python scripts/dev_setup.py --serve` validates that local configuration before starting a loopback server. An existing configuration is not overwritten unless `--force` is explicit; regenerating credentials can invalidate older local records, so use a new disposable database or clean up first. The script does not inherit production API settings or database connections.

Stop the API before removing `.env.development` and `api/hca_development.db` for local cleanup. These files contain disposable development state; do not run cleanup against another deployment. The self-contained demo uses a temporary directory and removes it automatically. Browser and Mac local data have separate cleanup instructions in their component READMEs.

## Checks

| Area | Command from the repository root | Scope |
| --- | --- | --- |
| API | `cd api && ../.venv/bin/pytest -q` | Disposable SQLite by default; Postgres-specific skips remain visible |
| Postgres API | Set `HCA_TEST_DATABASE_URL` to a local disposable test database, then run the API suite | Locks, delayed event commit ordering and multiprocess paths |
| Node | `cd extension && npm ci && npm test` | Signed calls, queue/key/state races, revocation and reconnect |
| Chromium | `cd extension && npx playwright install chromium && npm run test:browser` | Actual DOM, IndexedDB, worker restart and all controls |
| Swift | `cd macos && swift test` | Complete concurrency checking, warnings as errors and real loopback TCP tests |
| Developer Mac bundle | `bash macos/scripts/bundle.sh` | Local bundle, archive and checksum; no notarization claim |
| Python lint | `.venv/bin/ruff check api .github/gates scripts` | Declared repository profile |
| API contract | `.venv/bin/python .github/gates/check_openapi_parity.py` | Complete saved-versus-served contract |
| OpenAPI syntax | `.venv/bin/openapi-spec-validator api/openapi.yaml` | Document validity |
| Dependencies | `.venv/bin/pip-audit -r api/requirements-dev.lock.txt --disable-pip --no-deps --progress-spinner off` | Known vulnerabilities at run time |
| Credentials/history | `.venv/bin/python .github/gates/check_secrets.py --scanner /tmp/hca-tools/gitleaks` | Current source and complete fetched history; exact reviewed exceptions |
| Client-data hygiene | `.venv/bin/python .github/gates/check_client_data.py` | Tracked text against the maintained public-safe denylist |
| Product isolation | `.venv/bin/python .github/gates/check_manifest.py` | Privileged personal operations excluded from product source |
| Current document links | `.venv/bin/python .github/gates/check_doc_links.py` | Relative files and heading anchors; dated history is preserved |
| Prose | `.venv/bin/python .github/gates/check_no_dashes.py` | No U+2013/U+2014 characters in tracked text |

For a separate local Postgres test server, an example container command is:

```sh
docker run --rm --name hca-test-postgres -e POSTGRES_USER=hca -e POSTGRES_PASSWORD=hca -e POSTGRES_DB=hca_test -p 127.0.0.1:5432:5432 postgres:16
```

In a second terminal, activate the environment and run:

```sh
cd api
HCA_TEST_DATABASE_URL=postgresql+psycopg2://hca:hca@127.0.0.1:5432/hca_test ../.venv/bin/pytest -q
```

The example credentials are only for that loopback disposable container. Stop it with Control-C when finished. Tests reject remote/production-looking database URLs before app imports and rebuild their database. Each independent CI process needs its own test database. Real HTTP/TCP tests require loopback socket permission. A passing SQLite suite does not substitute for Postgres checks.

## API changes

FastAPI models and route declarations are authoritative. After changing a shape, constraint, security declaration, header or route:

```sh
.venv/bin/python .github/gates/export_openapi.py
.venv/bin/python .github/gates/check_openapi_parity.py
.venv/bin/openapi-spec-validator api/openapi.yaml
```

Review the complete schema diff and clients. Contract tests include real responses and deliberate drift; do not add an exception to conceal a mismatch. Preserve the immutable migration chain and test upgrading a previous schema. API response timestamps use an explicit UTC zone without rewriting idempotency fingerprints or stored input.

## Security and evidence

Do not put live tokens, keys, personal/client records or internal endpoints in source, issues, logs or screenshots. Use synthetic data and preserve upstream notices/publication rights. A public OAuth client ID, manifest public key or signing test vector is not a production secret. A scanner exception must identify its exact public test material and rationale.

Install the checksum-pinned scanner on Linux x64 or Mac arm64 with `python .github/gates/install_gitleaks.py --output /tmp/hca-tools/gitleaks`; other architectures require a separately verified Gitleaks 8.30.1 binary. Fetch complete Git history before scanning. Do not put real credentials in a filename, scanner exception or test fixture.

The workflow's explicit Bash shell preserves pipeline failures, and Swift diagnostics are retained. Pinned references, scans, SBOM/provenance and security analyses establish evidence for their revision and scope, not certification or an absence of every vulnerability. Read [current status](docs/STATUS.md), including outstanding alert dispositions, before making release claims.

The owner-reported manual validation is recorded separately from automated and production evidence. Keep historical records intact and label their date/source. State AI assistance and the maintainer's review responsibility accurately; do not invent authorship, customer outcomes, measured savings or performance results. Conduct/support channels and private security reports have distinct purposes. See [Security](SECURITY.md) and [Code of Conduct](CODE_OF_CONDUCT.md).
