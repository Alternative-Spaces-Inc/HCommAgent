# Security and hiring-readiness remediation

This is a source-remediation candidate for the October 12 review of archive commit `0f78e630e99444f85d666409aa4d6f4537e88331`. The original review contained 43 actionable changes, including scope and operational requirements. Source fixes, tests and documentation do not establish that the new version is deployed or formally certified.

All high-priority findings have code changes and targeted regressions. This matrix distinguishes repository implementation from checks that require the actual cloud, browser account, operating system or maintainer. Read [Operations](OPERATIONS.md) before merging: the existing main deployment trigger can run migrations and production rollout. Separate migration credentials and webhook encryption secrets must be provisioned first.

| Review item | Change | Completion boundary | Evidence entry point |
| --- | --- | --- | --- |
| 01 | Bind idempotency to the requested resource | Implemented in the review branch; validate exact-commit CI before rollout. | [api/tests/test_durability_regressions.py](../api/tests/test_durability_regressions.py) |
| 02 | Persist idempotency atomically with the business change | Implemented in the review branch; validate exact-commit CI before rollout. | [api/tests/test_durability_regressions.py](../api/tests/test_durability_regressions.py) |
| 03 | Commit durable events with their state changes | Implemented in the review branch; validate exact-commit CI before rollout. | [api/app/events.py](../api/app/events.py) |
| 04 | Make owner removal revoke existing authorization | Implemented in the review branch; validate exact-commit CI before rollout. | [api/app/auth.py](../api/app/auth.py) |
| 05 | Make owner allowlists and production authentication fail closed | Implemented in the review branch; validate exact-commit CI before rollout. | [api/app/config.py](../api/app/config.py) |
| 06 | Enforce stream revocation on a clock, independent of traffic | Implemented in the review branch; validate exact-commit CI before rollout. | [api/tests/test_independent_security_review.py](../api/tests/test_independent_security_review.py) |
| 07 | Keep nonces until the signed request can no longer be accepted | Implemented in the review branch; validate exact-commit CI before rollout. | [api/tests/test_security_boundaries.py](../api/tests/test_security_boundaries.py) |
| 08 | Enforce deadlines in the answer transaction | Implemented in the review branch; validate exact-commit CI before rollout. | [api/tests/test_durability_regressions.py](../api/tests/test_durability_regressions.py) |
| 09 | Fence and renew job leases | Implemented in the review branch; validate exact-commit CI before rollout. | [api/app/jobs.py](../api/app/jobs.py) |
| 10 | Add a true webhook wall-clock deadline and bounded response consumption | Implemented in the review branch; validate exact-commit CI before rollout. | [api/app/webhooks.py](../api/app/webhooks.py) |
| 11 | Authenticate the webhook delivery deduplication ID | Implemented in the review branch; validate exact-commit CI before rollout. | [docs/webhooks.md](../docs/webhooks.md) |
| 12 | Make authoritative audit identity server-derived | Implemented in the review branch; validate exact-commit CI before rollout. | [api/app/requests_api.py](../api/app/requests_api.py) |
| 13 | Bound API resources and paginate reads | Implemented in the review branch; validate exact-commit CI before rollout. | [api/app/limits.py](../api/app/limits.py) |
| 14 | Implement data lifecycle and backlog cleanup | Implemented bounded transient retention and completed-record export/purge; profile/destination/backup/log retention remains operator-defined. | [api/app/data_api.py](../api/app/data_api.py) |
| 15 | Validate client API origins and redirect behavior | Implemented in the review branch; validate exact-commit CI before rollout. | [extension/README.md](../extension/README.md) |
| 16 | Make extension device-key creation single flight and improve custody | Implemented in the review branch; validate exact-commit CI before rollout. | [extension/lib/devicekey.js](../extension/lib/devicekey.js) |
| 17 | Make the offline queue atomic, bounded, and tied to identity/origin | Implemented in the review branch; validate exact-commit CI before rollout. | [extension/lib/offlinequeue.js](../extension/lib/offlinequeue.js) |
| 18 | Reset extension caches, streams, and cursors when configuration changes | Implemented in the review branch; validate exact-commit CI before rollout. | [extension/lib/state.js](../extension/lib/state.js) |
| 19 | Resolve the supported-control mismatch | Implemented in the review branch; validate exact-commit CI before rollout. | [extension/lib/controls.js](../extension/lib/controls.js) |
| 20 | Harden the Mac OAuth callback parser and listener | Implemented in the review branch; validate exact-commit CI before rollout. | [macos/Tests/HCACoreTests/OAuthLoopbackListenerTests.swift](../macos/Tests/HCACoreTests/OAuthLoopbackListenerTests.swift) |
| 21 | Resolve Swift concurrency warnings through isolation | Implemented in the review branch; validate exact-commit CI before rollout. | [macos/Package.swift](../macos/Package.swift) |
| 22 | Preserve Mac notification action mappings across restart and bound categories | Implemented recovery from authorized inbox, matching origin/options and bounded categories; native OS lifecycle checks remain. | [macos/Sources/HCACore/NotificationRecovery.swift](../macos/Sources/HCACore/NotificationRecovery.swift) |
| 23 | Improve decision UI safety and accessibility | Implemented safe selection, full decision context and accessible errors; manual assistive-technology verification remains. | [extension/docs/decision-review.png](../extension/docs/decision-review.png) |
| 24 | Preserve Swift test exit status in CI | Implemented in the review branch; validate exact-commit CI before rollout. | [.github/workflows/gates.yml](../.github/workflows/gates.yml) |
| 25 | Enforce actual OpenAPI parity | Implemented in the review branch; validate exact-commit CI before rollout. | [.github/gates/check_openapi_parity.py](../.github/gates/check_openapi_parity.py) |
| 26 | Add the missing fault and concurrency regression matrix | Regression suites added; exact-commit Postgres and CI evidence pending. | [api/tests/test_durability_regressions.py](../api/tests/test_durability_regressions.py) |
| 27 | Isolate test databases and background workers | Implemented in the review branch; validate exact-commit CI before rollout. | [api/tests/conftest.py](../api/tests/conftest.py) |
| 28 | Make dependencies and tooling reproducible | Implemented in the review branch; validate exact-commit CI before rollout. | [api/requirements-dev.lock](../api/requirements-dev.lock) |
| 29 | Harden the release container | Non-root digest-pinned image, scan/SBOM/provenance CI added; exact image scan pending. | [api/Dockerfile](../api/Dockerfile) |
| 30 | Add real secret scanning and historical hygiene review | Implemented in the review branch; validate exact-commit CI before rollout. | [.github/gates/check_client_data.py](../.github/gates/check_client_data.py) |
| 31 | Pin CI actions and critical build tools immutably | Implemented in the review branch; validate exact-commit CI before rollout. | [.github/workflows/gates.yml](../.github/workflows/gates.yml) |
| 32 | Validate and smoke a candidate revision before routing all traffic | Build-specific candidate tag and exact revision promotion added; production live human/receiver proof remains. | [cloudbuild.yaml](../cloudbuild.yaml) |
| 33 | Narrow migration privileges and make recovery safe | Separate migration service account and database secret configured, scoped recovery; actual IAM/grants/restore proof remains. | [api/alembic/env.py](../api/alembic/env.py) |
| 34 | Fix the manual source-upload build path | Implemented in the review branch; validate exact-commit CI before rollout. | [.gcloudignore](../.gcloudignore) |
| 35 | Supply a complete safe local quickstart | Implemented in the review branch; validate exact-commit CI before rollout. | [scripts/dev_setup.py](../scripts/dev_setup.py) |
| 36 | Correct privacy disclosures and implement promised data handling | Implemented in the review branch; validate exact-commit CI before rollout. | [api/app/privacy.py](../api/app/privacy.py) |
| 37 | Repair governance contacts and code ownership | Implemented in the review branch; validate exact-commit CI before rollout. | [SECURITY.md](../SECURITY.md) |
| 38 | Verify and document repository enforcement settings | Live protection/scanning verified and strengthened; green exact-commit CI pending. | [docs/REPOSITORY_SETTINGS_2026-10-12.json](../docs/REPOSITORY_SETTINGS_2026-10-12.json) |
| 39 | Remove privileged personal-agent operations from the product tree | Removed privileged tooling from product head; local copy and Git history retained. Private operations repo/history rewrite not performed. | [.github/gates/check_manifest.py](../.github/gates/check_manifest.py) |
| 40 | Reconcile documentation with implementation and evidence | Implemented in the review branch; validate exact-commit CI before rollout. | [README.md](../README.md) |
| 41 | Add a short, credential-free portfolio demonstration and case study | Credential-free real HTTP/signed webhook demonstration and actual browser screenshot added; personal attribution requires owner review. | [docs/PORTFOLIO.md](../docs/PORTFOLIO.md) |
| 42 | Make releases installable and versioned coherently | Versioned developer-only Mac release path implemented; public Developer ID/notarized distribution is outside scope. | [macos/README.md](../macos/README.md) |
| 43 | Document operational limits, threat model, and recovery evidence | Versioned keyring/pepper overlap and operations/threat model added; real alerts, restore, load/soak and production evidence remain. | [docs/OPERATIONS.md](../docs/OPERATIONS.md) |

## Verification

Local targeted security, encryption/data handling and independent-review regressions passed. The synthetic demonstration ran over real HTTP and verified signed delivery, receiver deduplication, idempotent retry and an audit read. Chrome helper/worker tests and Chromium DOM/IndexedDB integration passed. Mac strict concurrency tests and a local ad-hoc release bundle passed. The local integrated API suite passed 250 tests with 4 database-specific skips and 91.32 percent coverage. The Mac suite passed 67 tests with strict concurrency. Postgres, image and exact-commit GitHub checks must be attached to the pull request; do not interpret these earlier component results as a final release certification.

The local PostgreSQL client installation lacks a server binary and Docker is not running; Postgres 16 and container checks run in GitHub CI. Repository settings were inspected live, including the full language CodeQL validation, secret scanning/push protection and private vulnerability reporting. See the dated settings record for the actual enforcement state and the explicit solo-maintainer review exception.

## Supported scope

The Chrome surface supports all six controls. Mac supports approve/deny and radio, with other controls directed to Chrome. API delivery modes are signed webhook and polling; an unimplemented websocket transport is no longer advertised. The demo simulates a human signer in a disposable development environment. Real Google owner authorization, Chrome scheduling, native notification/Keychain/login behavior, cloud restore/load/alerting, and public notarized Mac distribution require their own evidence. No user credentials, production migration, traffic change or paid signing enrollment was performed by these local tests.

Personal agent operations were removed from the product head, not retroactively erased from public Git history. No exposed live credential was found by the pattern scanner; exact public test-key material has a fingerprint-based exception. A pattern scan does not replace investigation of client business data or a formal security assessment.
