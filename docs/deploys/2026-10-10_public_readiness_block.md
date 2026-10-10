# Public readiness block: governance, hygiene, license, README (2026-10-10)

## Result

Nine pull requests merged to main between 17:37Z and 18:29Z, closing eight of the ten gaps of the public-readiness review (docs/reviews/2026-10-10_public_readiness_review.md). Every merge went through the full CI gate set; every build on main succeeded or is recorded below with its outcome.

| PR | Content | main | Merged (UTC) | Review gaps closed |
| --- | --- | --- | --- | --- |
| 80 | Governance files: SECURITY.md, CODE_OF_CONDUCT.md, issue and PR templates, dependabot.yml, CODEOWNERS | 1d159124 | 17:37 | 3 (part) |
| 81 | Hygiene and CI: identifier scrub, coverage gate 85 percent on the API suite, Swift tests job on macOS, SQLite test fix | 4b5371a8 | 17:38 | 2, 6, 7, 9 |
| 82 | LICENSE: Apache License 2.0 (owner decision, copyright 2026 Alternative Spaces Inc.) | 8fa56cfb | 17:55 | 3 (complete; repository metadata now reports Apache-2.0) |
| 87 | README front door and docs/ARCHITECTURE.md with Mermaid flowchart and sequence diagram | 9772d068 | 18:17 | 4, 8 |
| 78 | The review itself, under docs/reviews | c73429b5 | 18:20 | record |
| 86 | Dependabot: uvicorn 0.54.0, sqlalchemy 2.1.4, alembic 1.20.0, pydantic 2.13.5, httpx 0.28.1, psycopg2-binary 2.9.13, pytest-timeout 2.4.0 | ae3f18f2 | 18:27 | hygiene |
| 83 | Dependabot: actions/setup-node 4 to 7 | a6c56c83 | 18:29 | hygiene |
| 84 | Dependabot: actions/setup-python 5 to 7 | bff50c9c | 18:44 | hygiene |
| 85 | Dependabot: actions/checkout 4 to 7 | b5c07a0d | 18:5x | hygiene |
| 89 | Hotfix: bounded database connection pool (3 plus 3 per process, 10 s timeout, 300 s recycle, HCA_DB_* overrides) | 07cda84c | 19:08 | incident below |

## Builds on main

| Build | main | Outcome | Live revision |
| --- | --- | --- | --- |
| 3ae39c11 | c73429b5 (PR 78) | SUCCESS | hca-api-service-00061 |
| 92d896c9 | ae3f18f2 (PR 86, seven package bumps) | FAILURE at smoke-live: GET after cancel 500 | 00061 deployed before the smoke step |
| f0679f3e | a6c56c83 (PR 83) | FAILURE at migrate | none |
| 2a52c57a | bff50c9c (PR 84) | SUCCESS, smoke-live PASS | 00062 |
| 50a4650c | b5c07a0d (PR 85) | SUCCESS, smoke-live PASS | 00063 |
| c02bb4ae | 07cda84c (PR 89) | SUCCESS, smoke-live PASS including GET after cancel 200 | 00064 |

## Incident: connection slots exhausted

The service log for revision 00061 shows psycopg2 OperationalError: connection to the Cloud SQL socket failed, FATAL remaining connection slots are reserved for roles with privileges of the pg_use_reserved_connections role. The instance is the smallest shared-core tier with 25 slots. SQLAlchemy defaults (5 pooled plus 10 overflow per process, no recycle) across the draining revision, the new revision, the migrate job and the smoke test exceeded them during the burst of five deploys in one hour; the next two builds on the same packages passed, so the bumps themselves were not the cause. Two fixes, both landed: PR 89 bounds the pool (3 plus 3, 10 s timeout, 300 s recycle, tunable by HCA_DB_POOL_SIZE, HCA_DB_MAX_OVERFLOW, HCA_DB_POOL_TIMEOUT, HCA_DB_POOL_RECYCLE), and the owner raised the database flag max_connections to 50 from the Mac Terminal (gcloud sql instances patch, instance restart, verified RUNNABLE with the flag at 19:0x). Lesson: read the smoke-live lines and the service log, not the deploy step; a deploy step SUCCESS left a 500-answering revision live for the twenty minutes until the next build.

Earlier builds of the block (PR 80, 81, 82, 87): all SUCCESS, revisions 00057 through 00060, smoke-live PASS on each. Docs-only merges still run the full pipeline because the trigger is on main, so each produced a revision with identical code.

## Verification

- Repository metadata: GET repository returned license spdx_id Apache-2.0 after PR 82 (null before).
- Local suite on SQLite after PR 81: 105 passed, 1 skipped (the review had found 1 failed, 104 passed).
- Leaked identifier grep from the review (five patterns): zero occurrences after PR 81; the client-data gate denylist stays empty by design and the five patterns are checked in the hygiene test instead.
- README and architecture statements were each checked against the module they cite before PR 87 opened; the dash gate and link-target check passed.
- Dependabot PRs 83 to 86: all ten CI jobs success on each head (CodeQL neutral: it does not run on Dependabot branches).

## Branch protection on main (gap 1, closed 19:1x)

Applied through the GitHub API on the owner's selection and read back: pull request required before merge (0 approvals, the owner merges his own PRs), nine required status checks (API Tests (Postgres), Extension Tests (Node 20), Swift Tests (macOS), Python Lint (ruff), Python Dependency Audit (pip-audit), Validate OpenAPI Specification, Check No Dash Characters, Check Client Data Hygiene, Check Bootstrap Manifest Integrity) with the branch required up to date with main before merge, the four CodeQL analyses advisory so Dependabot branches are not blocked, conversation resolution required, force pushes and deletions refused, enforced for administrators. Eight of the ten review gaps are now closed; the two below remain.

## Still open from the review

| Gap | State | Next action |
| --- | --- | --- |
| 5 CodeQL alert 11, py/weak-sensitive-data-hashing, api/app/apikeys.py line 39 | open, high | The hashed value is a 192-bit random secret, so a key derivation function adds no security over SHA-256 (preimage resistance is the only property needed). Owner selection pending: dismiss as false positive with that justification (as alert 7 was), or switch to HMAC-SHA256 with a server-side pepper to satisfy the rule |
| 10 hardening program P20 to P27 | open by design | P20 plan v1 is PR 73 |

## Lessons

- A worker brief that asks for a README in one pass returned nothing four times; writing it by hand from the code took one pass. Long synthesis documents are written by the seat, not delegated.
- Dependabot opened its first PRs within a minute of dependabot.yml landing; review and merge them in the same block so main does not drift behind the gate images.
- Stacked docs merges each trigger a full deploy; harmless but slow. A path filter on the trigger is a P25 item.
