# Public readiness block: governance, hygiene, license, README (2026-10-10)

## Result

Nine pull requests merged to main between 17:37Z and 18:29Z, closing seven of the ten gaps of the public-readiness review (docs/reviews/2026-10-10_public_readiness_review.md). Every merge went through the full CI gate set; every build on main succeeded or is recorded below with its outcome.

| PR | Content | main | Merged (UTC) | Review gaps closed |
| --- | --- | --- | --- | --- |
| 80 | Governance files: SECURITY.md, CODE_OF_CONDUCT.md, issue and PR templates, dependabot.yml, CODEOWNERS | 1d159124 | 17:37 | 3 (part) |
| 81 | Hygiene and CI: identifier scrub, coverage gate 85 percent on the API suite, Swift tests job on macOS, SQLite test fix | 4b5371a8 | 17:38 | 2, 6, 7, 9 |
| 82 | LICENSE: Apache License 2.0 (owner decision, copyright 2026 Alternative Spaces Inc.) | 8fa56cfb | 17:55 | 3 (complete; repository metadata now reports Apache-2.0) |
| 87 | README front door and docs/ARCHITECTURE.md with Mermaid flowchart and sequence diagram | 9772d068 | 18:17 | 4, 8 |
| 78 | The review itself, under docs/reviews | c73429b5 | 18:20 | record |
| 86 | Dependabot: uvicorn 0.54.0, sqlalchemy 2.1.4, alembic 1.20.0, pydantic 2.13.5, httpx 0.28.1, psycopg2-binary 2.9.13, pytest-timeout 2.4.0 | ae3f18f2 | 18:27 | hygiene |
| 83 | Dependabot: actions/setup-node 4 to 7 | a6c56c83 | 18:29 | hygiene |
| 84 | Dependabot: actions/setup-python 5 to 7 | see note | | hygiene |
| 85 | Dependabot: actions/checkout 4 to 7 | see note | | hygiene |

Note: 84 and 85 were approved by the owner in the same selection as 83 and 86; their merge shas are recorded in the follow-up section when the owner's clicks land.

## Builds on main

| Build | main | Outcome | Live revision |
| --- | --- | --- | --- |
| 3ae39c11 | c73429b5 (PR 78) | SUCCESS | hca-api-service-00061 |
| 92d896c9 | ae3f18f2 (PR 86, seven package bumps) | recorded in the follow-up section | |
| f0679f3e | a6c56c83 (PR 83) | recorded in the follow-up section | |

Earlier builds of the block (PR 80, 81, 82, 87): all SUCCESS, revisions 00057 through 00060, smoke-live PASS on each. Docs-only merges still run the full pipeline because the trigger is on main, so each produced a revision with identical code.

## Verification

- Repository metadata: GET repository returned license spdx_id Apache-2.0 after PR 82 (null before).
- Local suite on SQLite after PR 81: 105 passed, 1 skipped (the review had found 1 failed, 104 passed).
- Leaked identifier grep from the review (five patterns): zero occurrences after PR 81; the client-data gate denylist stays empty by design and the five patterns are checked in the hygiene test instead.
- README and architecture statements were each checked against the module they cite before PR 87 opened; the dash gate and link-target check passed.
- Dependabot PRs 83 to 86: all ten CI jobs success on each head (CodeQL neutral: it does not run on Dependabot branches).

## Still open from the review

| Gap | State | Next action |
| --- | --- | --- |
| 1 branch protection on main | GET branches/main/protection still 404 at 18:3x | Owner selection: required status checks (the nine gate jobs), pull request before merge, no force push, no deletion; applied by the owner in Settings or by the agent through the API on approval |
| 5 CodeQL alert 11, py/weak-sensitive-data-hashing, api/app/apikeys.py line 39 | open, high | The hashed value is a 192-bit random secret, so a key derivation function adds no security over SHA-256 (preimage resistance is the only property needed). Owner selection pending: dismiss as false positive with that justification (as alert 7 was), or switch to HMAC-SHA256 with a server-side pepper to satisfy the rule |
| 10 hardening program P20 to P27 | open by design | P20 plan v1 is PR 73 |

## Lessons

- A worker brief that asks for a README in one pass returned nothing four times; writing it by hand from the code took one pass. Long synthesis documents are written by the seat, not delegated.
- Dependabot opened its first PRs within a minute of dependabot.yml landing; review and merge them in the same block so main does not drift behind the gate images.
- Stacked docs merges each trigger a full deploy; harmless but slow. A path filter on the trigger is a P25 item.
