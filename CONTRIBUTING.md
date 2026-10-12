# Contributing

Use a branch and a pull request. Describe the concrete behavior change, relevant tests, and remaining limits. Security changes should include the failure case they prevent. Review the exact candidate revision and its CI before merging; a historical green run is not evidence for new code. Repository settings and deploy authorization are separate from files in a branch.

## Reproducible environment

Python 3.12, Node 24 or later, and macOS 14 or later with the Swift toolchain are the supported development paths. CI uses Python 3.12, Node 24 and a macOS runner. From the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r api/requirements-dev.lock
python scripts/dev_setup.py
python scripts/demo_workflow.py
```

`api/requirements.lock` contains runtime dependencies; `api/requirements-dev.lock` includes validation tools. Update their source inputs and regenerate both locks together when required. The extension's browser tooling is pinned in `extension/package-lock.json`; use `npm ci`.

## Checks to run

| Area | Command from the repository root | Scope |
| --- | --- | --- |
| API | `cd api && ../.venv/bin/pytest -q` | Disposable SQLite by default; mock webhook/DNS fixtures |
| API with Postgres | Set `HCA_TEST_DATABASE_URL` to a local `test_*` or `*_test` database, then run the API suite | Row locks, event commit ordering, real multi-process paths |
| Extension | `cd extension && npm ci && npm test` | Deterministic queue, key, authorization and stream races |
| Actual Chromium | `cd extension && npx playwright install chromium && npm run test:browser` | Real DOM, IndexedDB, cross-context startup and worker restart |
| Swift | `cd macos && swift test` | Strict concurrency, signing, notifications and real loopback TCP tests |
| Developer Mac build | `bash macos/scripts/bundle.sh` | Local bundle/archive/checksum; no notarization claim |
| Python lint | `.venv/bin/ruff check api .github/gates scripts` | Repository's declared lint profile |
| Dependency audit | `.venv/bin/pip-audit -r api/requirements.txt --progress-spinner off` | Known vulnerability database at run time |
| OpenAPI | `.venv/bin/python .github/gates/check_openapi_parity.py` | Entire generated contract equals the saved schema |
| Credentials/history | `.venv/bin/python .github/gates/check_client_data.py --history` | Credential patterns plus optional client-data denylist |
| Product isolation | `.venv/bin/python .github/gates/check_manifest.py` | Privileged `bootstrap`, `platform`, `agents` directories absent |
| Prose gate | `.venv/bin/python .github/gates/check_no_dashes.py` | No U+2013/U+2014 characters in tracked text |

Tests refuse remote Postgres and production-looking database names. A default run creates a temporary SQLite database; module fixtures rebuild it and stop workers/listeners between modules. Webhook and DNS transports are restored after tests. This does not make arbitrary parallel test execution safe against a shared database: each CI job or independent process needs its own disposable database. Real HTTP/TCP tests need loopback socket permission. Postgres-only skips must remain visible in SQLite results.

The [extension](extension/README.md) and [Mac](macos/README.md) documents distinguish executable integration checks from manual Google OAuth, native notification, Keychain and platform scheduling checks. A failing subprocess must fail its CI step. Swift diagnostics are retained, and the workflow's explicit Bash shell preserves pipeline exit failures.

## API contract changes

The FastAPI models and route declarations are authoritative. After changing an API shape, constraint, security declaration, header or route:

```sh
.venv/bin/python .github/gates/export_openapi.py
.venv/bin/python .github/gates/check_openapi_parity.py
.venv/bin/openapi-spec-validator api/openapi.yaml
```

Review the complete `api/openapi.yaml` diff and update clients and request/response tests. Do not add an exception to conceal an unimplemented route or schema mismatch.

## Security and automation

CI actions and critical build images use immutable references where configured. Dependabot proposes dependency, action and image updates; assess and test them before merging. The container gate scans the candidate image and exports an SBOM. A scan or CodeQL success is evidence for its tool and revision, not a general security certification. Do not add blanket vulnerability exceptions to obtain a green check. If a justified temporary exception is necessary, record the finding, affected scope, owner, expiry and compensating control in the PR.

The credential gate masks findings, scans current files and Git history, and allows only exact documented public test-key fingerprints. Public OAuth client IDs and public signature test vectors are not production secrets. Never paste live keys, owner tokens, internal hosts or personal/client data into source, issues, test output or screenshots. Rotate exposed credentials before discussing history rewrite, and coordinate any destructive rewrite with maintainers.

`Check Bootstrap Manifest Integrity` retains its historical CI name for rules compatibility; it now checks product isolation. Privileged personal-agent workflows are outside this repository. Do not reintroduce tools that infer human authorization from a free-form approval string.

## Documentation and ownership

Use synthetic examples. Distinguish implemented behavior, local tests, live evidence and future work. Current documents are indexed in [docs/INDEX.md](docs/INDEX.md); dated plans and deployment records remain historical references. Prefer CI-derived results to static test counts. State any AI assistance and your review responsibility clearly, without attributing work to someone whose contribution has not been verified.

Code ownership is declared in [.github/CODEOWNERS](.github/CODEOWNERS). See [Security](SECURITY.md) for private reports and the [Code of Conduct](CODE_OF_CONDUCT.md) for conduct concerns. Source ownership does not prove protected-branch rules are active; consult the [dated repository settings record](docs/REPOSITORY_SETTINGS_2026-10-12.json).
