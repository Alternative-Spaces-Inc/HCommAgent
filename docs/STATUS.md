# Current status and evidence

This page describes the supported source behavior for the 0.3.0 developer release. It separates implementation, executable checks, owner-reported validation and production/operator evidence. It does not claim formal certification or that publishing source updated a live deployment.

## Supported behavior

- Current tenant owners authorize decisions. Caller keys scope agent access; registered P-256 devices sign protected human calls. `audience` and client context are descriptive.
- Chrome answers radio, checkbox, approve/deny, dropdown, chat and row confirmation. Mac answers radio and approve/deny, with other controls directed to Chrome.
- Resource-bound retries, state/audit/events/outbox and receipts share transactions. Deadlines are enforced during answering; workers renew/fence leases; ordered durable events and inbox reconciliation recover disconnects.
- API bodies, nested data, pages, registrations, active requests and actor/tenant budgets are bounded. Explicit export/purge preserves pending decisions, queued deliveries and live leases. Transient cleanup is bounded.
- Full saved-versus-served OpenAPI parity includes schemas, constraints, bodies, security and headers. Signed receiver IDs and transactional receiver ledgers address repeated deliveries; delivery remains at least once.

See [Architecture](ARCHITECTURE.md), [Threat model](THREAT_MODEL.md), [Chrome](../extension/README.md) and [Mac](../macos/README.md) for exact boundaries.

## Dated evidence

| Evidence | What it establishes | Boundary |
| --- | --- | --- |
| [Approval-boundary remediation](SECURITY_REMEDIATION_01_12.md) | First twelve review fixes and regressions | Exact source/CI evidence; rollout is separate |
| [Resource/client remediation](SECURITY_REMEDIATION_13_23.md) | Bounded API lifecycle and client recovery/context controls | Synthetic and platform tests have distinct scope |
| [PR7](https://github.com/Alternative-Spaces-Inc/HCommAgent/pull/7) | Tested authorization/durability implementation | Merged source is not proof of live configuration |
| [PR9](https://github.com/Alternative-Spaces-Inc/HCommAgent/pull/9) | Client/resource work and complete contract parity | Final release integration needs its own exact-head checks |
| [Repository/operations remediation](SECURITY_REMEDIATION_24_43.md) | Items 24 to 43, source validation and operator prerequisites | Exact final CI and release assets must accompany publication |
| [Owner manual confirmation](reviews/2026-10-12_manual_validation_approval.md) | Owner reported manual sign-in/client/lifecycle/accessibility validation finished and approved | Owner-reported confirmation, without invented independent logs |
| [Repository enforcement verification](reviews/2026-10-12_enforcement_verification.md) | Dated branch/scanning/private-reporting configuration and solo-maintainer exception | Historical settings, not immutable future enforcement |
| [Security alert triage addendum](reviews/2026-10-12_security_alert_triage.md) | Severity distinction and disposition of existing findings | Passing analysis does not mean zero open alerts |
| [Synthetic Chrome screenshot](../extension/docs/decision-review.png) | Actual popup context rendered with synthetic data | No production account or deployment shown |

The published PR9 source at `9213d9a6b0439c1997606daaacbf08d2013525ce` passed 315 Postgres tests without skips and 93.40 percent coverage, plus all nine required quality checks, 61 Node tests and 68 Swift tests. This is dated evidence from [run 38294143501](https://github.com/Alternative-Spaces-Inc/HCommAgent/actions/runs/38294143501), not a permanent count or the final combined release. Later source/lock/pipeline changes must link their own passing runs. Earlier Chromium and real HTTP checks and the owner confirmation remain identified in the remediation records.

## Final-candidate checks, October 12, 2026

[PR15](https://github.com/Alternative-Spaces-Inc/HCommAgent/pull/15) source
`9eeadbea6ebcc75c6338bcf9db77a3afd9a42d4f` passed all **13 quality checks** in
[run 38306535527](https://github.com/Alternative-Spaces-Inc/HCommAgent/actions/runs/38306535527):

- Postgres: 428 passed, one SQLite-specific skip, 93.41 percent coverage.
- SQLite/real HTTP: 425 passed, four Postgres-only skips, 92.13 percent coverage.
- Node: 61 tests plus actual Chromium flows. Swift: 68 tests, strict release
  compilation and actual developer bundling with the default Bash compatibility fix.
- Alpine container: non-root UID, read-only filesystem, isolated startup/HTTP and
  graceful SIGTERM checks passed; shutdown measured 0.427 seconds in that isolated
  CI run. This is not a production latency or service-level measurement.
- The selected Trivy HIGH/CRITICAL scan reported zero findings at that threshold;
  lower-severity information remains in the retained SBOM. This does not mean the
  image has no findings of any severity. Scan, provenance and runtime evidence have
  their documented export/digest boundaries.

The final published head subsequently passed all four CodeQL analyses, including
Swift; see the dated publication addendum below. The earlier candidate run does
not establish validation for later source/image/tool changes.

Main at `a392399` now requires the 13 quality checks from GitHub Actions app 15368,
with strict up-to-date checks. Administrator enforcement, force-push/deletion
restrictions, conversation resolution and the documented zero-approval
solo-maintainer exception are preserved. Active CodeQL ruleset `24952962` has no
bypass actors and requires CodeQL with security alerts at high or higher and
non-security alerts at error. It also blocks incomplete required analysis within
GitHub's documented scope. [The appended enforcement snapshot](reviews/2026-10-12_enforcement_verification.md#final-policy-update-october-12-2026)
records settings and limitations; this does not replace independent review.

## Source/release preparation

[Local setup and demo](../README.md#run-the-demonstration), [case study](CASE_STUDY.md), [release instructions](RELEASES.md), privacy/security guidance and current architecture support a clean-clone review. Release artifacts require the exact source revision, compatible API/client versions and checksums. Candidate version metadata is not evidence that a tag, release asset or Web Store update has been published.

Privileged personal operations are excluded from the supported product. Source isolation removes them from the product head; retained Git history and a private operations repository are separate matters. Inspect the exact reviewed revision and the isolation gate before relying on that separation.

The maintainer is [alfarmer](https://github.com/alfarmer). The [case study](CASE_STUDY.md) explicitly leaves personal-contribution claims for a factual owner statement. A dedicated confidential conduct-reporting address has not been supplied; [the conduct policy](../CODE_OF_CONDUCT.md) states that limitation rather than using a vulnerability form or inventing a contact.

## Production boundaries

Production still requires verified current configuration, migration/runtime privilege separation, schema compatibility, preserved/decrypted legacy webhook secrets, monitored budgets, restored backups, traffic rollout/rollback and load/soak evidence. Owners/operators must define backup/log/profile retention and deployment-specific contact details. Source export/purge does not erase backups or user-managed notification history.

The synthetic demo uses a temporary local database and a simulated signer. It does not exercise production Google authorization. The owner-approved client validation is recorded above and need not be repeated merely because documentation changed. Public notarized Mac distribution, automatic updates, enterprise connectors and formal compliance programs are outside the declared developer scope.

Read [Operations](OPERATIONS.md) and [deployment/rollback](runbooks/HCA_DEPLOY_ROLLBACK.md) before merging or promoting. A main merge can start a build, but `_APPROVED_COMMIT` defaults empty and the first guard refuses cloud mutation unless it matches the exact tested full source commit. No production rollout or key rotation is established by these checks.

## Publication addendum, October 12, 2026

[PR15](https://github.com/Alternative-Spaces-Inc/HCommAgent/pull/15) merged as
`402c27f` at `2026-10-12T21:37:07Z`. Its final head
`fc4add0571187bbca3ddb2008e7339f238275bc3` passed all **13 quality checks** in
[run 38309676048](https://github.com/Alternative-Spaces-Inc/HCommAgent/actions/runs/38309676048) and all **four CodeQL analyses** in
[run 38309671632](https://github.com/Alternative-Spaces-Inc/HCommAgent/actions/runs/38309671632), superseding the earlier pending Swift status.
Postgres recorded 428 passes, one SQLite-specific skip and 93.41 percent coverage;
SQLite recorded 425 passes, four Postgres-only skips and 92.13 percent coverage.
The final image SBOM contains 70 components and zero reported vulnerabilities
in the final Trivy results; the separate runtime smoke also passed. These are
exact-revision scan results, not a guarantee about future findings or deployments.

The [v0.3.0 developer prerelease](https://github.com/Alternative-Spaces-Inc/HCommAgent/releases/tag/v0.3.0) was published from tag
`v0.3.0` at that final head, with 14 assets whose checksums matched. The Mac
artifact is arm64, locally signed and not notarized. Public Mac distribution
and automatic updates remain outside the declared developer scope. Post-merge
verification found zero open CodeQL, secret-scanning and Dependabot alerts
at that time.

Cloud Build `85da85c3-7cd8-49c4-9152-aa35e216c060` stopped at the first
exact-commit approval guard; every later step remained `QUEUED`. That build
performed no production rollout. The owner-reported manual validation remains
approved, with no repeat requested. Personal-contribution details and a dedicated
confidential conduct contact still require the owner's factual input.

[PR17](https://github.com/Alternative-Spaces-Inc/HCommAgent/pull/17) is a separate
lock-filename maintenance change. Follow its [checks](https://github.com/Alternative-Spaces-Inc/HCommAgent/pull/17/checks)
for maintenance validation and [dependency update runs](https://github.com/Alternative-Spaces-Inc/HCommAgent/actions/workflows/dependabot/dependabot-updates)
for hosted updater results. It does not alter the frozen v0.3.0 release.
