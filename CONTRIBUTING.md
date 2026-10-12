# Contributing to the Human Collections Agent repo

Changes land through a branch and a pull request. Nobody pushes to main, and
the owner merges every PR after the gates are green; a merge to main deploys
the API through Cloud Build (see [cloudbuild.yaml](cloudbuild.yaml)). Agents
working in this repo follow the same path and never merge.

## Gates every PR must pass

CI runs [.github/workflows/gates.yml](.github/workflows/gates.yml). CodeQL
default setup also analyses Python, Swift, JavaScript and Actions on every PR
and reports as separate checks.

| Check | What it enforces | Script or command |
| --- | --- | --- |
| Check No Dash Characters | no em-dash (U+2014) or en-dash (U+2013) in any tracked text file | `python3 .github/gates/check_no_dashes.py` |
| Check Client Data Hygiene | no term from the owner-maintained denylist in any tracked file | `python3 .github/gates/check_client_data.py` |
| Check Bootstrap Manifest Integrity | every skill in `bootstrap/HCA_MANIFEST.yaml` exists and its sha256 matches | `python3 .github/gates/check_manifest.py` |
| Validate OpenAPI Specification | `api/openapi.yaml` is a valid OpenAPI document | `openapi-spec-validator api/openapi.yaml` |
| API Tests (Postgres) | pytest suite against Postgres 16 after `alembic upgrade head` | `cd api && pytest -v` |
| Extension Tests (Node 20) | Chrome extension unit tests | `cd extension && npm test` |
| Python Lint (ruff) | errors and bug-prone patterns in `api`, `.github/gates`, `scripts` (profile in [ruff.toml](ruff.toml)) | `ruff check api .github/gates scripts` |
| Python Dependency Audit (pip-audit) | no known vulnerability in the pinned `api/requirements-dev.txt` (runtime plus test pins) | `cd api && pip-audit -r requirements-dev.txt` |

Swift tests for the macOS app run on the owner's Mac, not in CI:
`cd macos && swift test`.

## Fixing the common failures

Dash gate: the output lists `path:line`. Replace the dash with a period,
comma, colon or parentheses. Prose in this repo never uses em or en dashes.

Client data gate: the output shows the file and line and a masked form of
the term. Remove the client name, project id, host or business fact; the
denylist itself is owner maintained and is not edited to make a PR pass.

Manifest gate: after changing a file under `bootstrap/skills/`, update its
`sha256` in `bootstrap/HCA_MANIFEST.yaml` to the value printed by
`shasum -a 256 bootstrap/skills/<file>` (the gate output also prints it as
"got").

Ruff: run `ruff check api .github/gates scripts --fix` for the automatic
fixes, then read what remains. The profile is deliberately narrow (E4, E7,
E9, F, W, B006, B904) so it does not impose formatting; widen it only in a
PR that fixes what the new rules find.

pip-audit: bump the flagged pin in `api/requirements.txt` or `api/requirements-dev.txt` to the fix version
pip-audit prints, run the pytest suite, and note the upgrade in the PR body.
A vulnerability without a fix version is recorded in the PR body and ignored
with `--ignore-vuln <id>` in the workflow, with the reason in a comment.

## Local development

API (SQLite is the default when `DATABASE_URL` is unset; CI uses Postgres):

    cd api && pip install -r requirements-dev.txt && alembic upgrade head && pytest -q

Extension:

    cd extension && npm test

macOS app (on a Mac with Xcode):

    cd macos && swift test && bash scripts/bundle.sh ~/hca-notifier

## Conventions

- Plans live under `docs/plans/`, proof and deploy records under
  `docs/deploys/`, decisions under `docs/decisions/`; every file is listed in
  [docs/INDEX.md](docs/INDEX.md) with one line.
- Evidence over assertion: a record cites the commit, PR number, Cloud Build
  id and the observed output, not the intent.
- Secrets never appear in files; name the Secret Manager entry or env var.
