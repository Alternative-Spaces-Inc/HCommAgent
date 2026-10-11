# Public Readiness Review: 2026-10-10

## Summary
The codebase presents a structured multi-surface architecture with comprehensive plans and deploy records, but requires key operational and repository hygiene updates before cold public presentation.
Top gap 1: Main branch lacks branch protection rules and CI quality gates failed on the latest push.
Top gap 2: Default local SQLite test suite execution fails on timestamp serialization in state machine idempotency tests.
Top gap 3: Missing essential repository governance files including LICENSE, SECURITY.md, and CODE_OF_CONDUCT.md.
Overall verdict: Conditionally ready pending governance file additions, local SQLite fix, and CI gate alignment.

## Evidence snapshot
- Commit: 5b872e5e9e83df090db474991c14115b2541ce2d
- Date: 2026-10-10
- Total commit count: 86
- Markdown file count: 48
- Test suite counts: 106 API pytest, 42 Chrome extension Node tests, 37 macOS Swift tests (185 total tests)
- CI pipeline status: HCA Quality Gates status completed with conclusion failure on latest push to main

## What already reads well
- Comprehensive production hardening roadmap documented in `docs/plans/HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md`.
- Rigorous deploy records and proof logs in `docs/deploys/` (e.g. `2026-10-09_p18_day2_path_b_proofs.md`, `2026-10-06_p16_mac_signing_identity.md`, `2026-10-05_p15_menu_asks_proof.md`).
- Exhaustive index maintaining single-line descriptions for every file in `docs/INDEX.md`.
- Detailed OpenAPI 3.0 specification skeleton in `api/openapi.yaml`.
- Clean modular layout across Python FastAPI backend (`api/`), Chrome MV3 extension (`extension/`), and macOS menu bar client (`macos/`).
- Clear project charter in `docs/HCA_CHARTER_v1.md`.
- Custom repository quality gates for dash verification, client data hygiene, and manifest integrity in `.github/workflows/gates.yml` and `.github/gates/`.

## Gaps ranked

| Rank | Gap | Evidence | Effort | Suggested fix |
| --- | --- | --- | --- | --- |
| 1 | Main branch lacks branch protection rules | GET `/branches/main/protection` returned HTTP 404 (`Branch not protected`) | S | Enable branch protection on `main` requiring green status checks and PR reviews before merging. |
| 2 | Local test suite fails on default SQLite setup | Command `pytest -q` in `api/` with SQLite produced `1 failed, 104 passed, 1 skipped, 5 warnings in 10.60s` (`test_answer_retry_with_same_key_replays_without_a_second_decision`) | S | Standardize datetime UTC string formatting in `api/app/idempotency.py` so responses match across SQLite and Postgres. |
| 3 | Missing standard repository governance files | `ls SECURITY.md LICENSE CODE_OF_CONDUCT.md .github/ISSUE_TEMPLATE .github/PULL_REQUEST_TEMPLATE.md .github/dependabot.yml` returned no files; GET `/` returned `"license": null`; GET `/dependabot/alerts` returned HTTP 403 | S | Add standard repository files: LICENSE, SECURITY.md, CODE_OF_CONDUCT.md, PR/issue templates, and enable Dependabot. |
| 4 | README.md lacks essential onboard documentation for outside engineers | `README.md` is 13 lines long; missing target audience, local start instructions, deployment summary, architecture diagram link, and security policy link | M | Expand `README.md` with product overview, architecture diagram link, copy-paste quickstart, deployment overview, and governance links. |
| 5 | Open high-severity code scanning security alert | GET `/code-scanning/alerts?state=open` returned alert #11 (`py/weak-sensitive-data-hashing` in `api/app/apikeys.py:32`) | S | Replace plain SHA256 hashing for sensitive keys with key derivation algorithms or proper salt handling. |
| 6 | macOS Swift unit test suite is not executed in CI | `.github/workflows/gates.yml` lacks a macOS test step (`grep -rE 'func test' macos | wc -l` shows 37 tests present) | M | Add a macOS runner step in `gates.yml` executing `cd macos && swift test`. |
| 7 | No test code coverage measurement or threshold in CI | `grep -rn 'cov' .github api/pyproject.toml api/requirements*.txt ruff.toml` returned no matches | S | Add `pytest-cov` to API requirements and configure minimum coverage threshold in CI gate. |
| 8 | Missing architecture diagram and interactive rendered API documentation link | `grep -rliE 'mermaid|architecture' docs README.md` returned 0 diagrams; `README.md` links only raw `api/openapi.yaml` | M | Add a Mermaid sequence diagram to `docs/ARCHITECTURE.md` and link a rendered API doc endpoint in README. |
| 9 | Leaked internal identifiers in codebase | `grep -rnE 'hca-prod|p2aj2fbycq|\.run\.app|@alternative-spaces\.com|alfarmer' .` returned 5 occurrences across 5 files (`bootstrap/skills/approved_run.py:35`, `bootstrap/skills/secret-parity-check.sh:57`, `docs/runbooks/HCA_FIRST_DEPLOY.md:126`, `extension/options.html:23`, `extension/tests/hostaccess.test.js:6`) | S | Sanitize leaked handles and specific host patterns to generic placeholders. |
| 10 | Production hardening program phases P19 to P27 remain open | `docs/plans/HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md` lists P19 (in progress) through P27 (open) | L | Complete remaining hardening phases in order, starting with P19 tenancy state machine and P20 durable delivery. |

## Hygiene findings
- Leaked identifiers: 5 total occurrences across 5 files:
  - `bootstrap/skills/approved_run.py:35`
  - `bootstrap/skills/secret-parity-check.sh:57`
  - `docs/runbooks/HCA_FIRST_DEPLOY.md:126`
  - `extension/options.html:23`
  - `extension/tests/hostaccess.test.js:6`
- Stray files at repository root: 0
- TODO / FIXME / XXX / HACK count: 0

## Program status
- Phases done: P1 through P18 documented across 19 deploy records in `docs/deploys/` (e.g. `2026-10-09_p18_day2_path_b_proofs.md`, `2026-10-06_p16_mac_signing_identity.md`, `2026-10-05_p15_menu_asks_proof.md`).
- Phase in progress: P19 Tenancy, authorization and request state machine (`docs/plans/HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md`).
- Phases open: P20 (Durable delivery), P21 (Webhooks and auth), P22 (Work orders and connectors), P23 (Chrome extension hardening), P24 (macOS hardening and release path), P25 (Containers, deployment, CI), P26 (Bootstrap isolation), P27 (Documentation and policy).

## Recommended order of work
1. PR 1: Fix SQLite idempotency test failure and enable branch protection on `main`.
2. PR 2: Resolve open CodeQL security alert in `api/app/apikeys.py`.
3. PR 3: Add standard repository governance files (`LICENSE`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.github/dependabot.yml`, issue and PR templates).
4. PR 4: Sanitize 5 leaked identifiers in bootstrap, docs, extension, and tests.
5. PR 5: Expand `README.md` with complete quickstart, architecture diagram link, deployment details, and governance links.
6. PR 6: Add macOS Swift test step to `.github/workflows/gates.yml` and configure pytest code coverage threshold.
7. PR 7: Complete Phase P19 state machine and OpenAPI CI parity gate.
