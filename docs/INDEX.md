# Documentation index

Start with [README](../README.md), [current status and evidence](STATUS.md), [case study](CASE_STUDY.md) and [Architecture](ARCHITECTURE.md). Current source guidance takes precedence over dated plans/deployment records.

## Current guidance

- [Threat model](THREAT_MODEL.md): assets, trust boundaries, defenses and assurance limits.
- [Operations](OPERATIONS.md): production prerequisites, rotation, retention, incident response and evidence requirements.
- [Caller API keys](api-keys.md): scopes, limits, lifecycle events and automation rotation.
- [Webhook receiver contract](webhooks.md): signing, authenticated deduplication, retry and rotation.
- [Release instructions](RELEASES.md): developer versioning, packaging, checksums, installation and upgrades.
- [Change log](../CHANGELOG.md): candidate source/API/client changes and compatibility.
- [Chrome instructions](../extension/README.md) and [Mac instructions](../macos/README.md): control support, registration, storage and platform validation.
- [Contributing](../CONTRIBUTING.md), [Security](../SECURITY.md) and [Code of Conduct](../CODE_OF_CONDUCT.md): development, supported updates and reporting.
- [Published OpenAPI](../api/openapi.yaml): full API contract, with complete parity checked against source.
- [Migration/recovery](runbooks/HCA_MIGRATION_RECOVERY.md): separate principals, bounded serialization and explicit scoped recovery.
- [Deployment/rollback](runbooks/HCA_DEPLOY_ROLLBACK.md): candidate validation, promotion and recovery; separate operator authorization remains necessary.

## Evidence

- [First twelve remediation items](SECURITY_REMEDIATION_01_12.md).
- [Resource/client remediation](SECURITY_REMEDIATION_13_23.md).
- [Repository/operations remediation](SECURITY_REMEDIATION_24_43.md).
- [Owner-reported manual validation](reviews/2026-10-12_manual_validation_approval.md).
- [Dated enforcement verification](reviews/2026-10-12_enforcement_verification.md).
- [Security alert triage addendum](reviews/2026-10-12_security_alert_triage.md).
- [Synthetic Chrome screenshot](../extension/docs/decision-review.png).

## Historical references

Files under `plans/`, `deploys/`, `decisions/`, `dispatches/`, `research/` and older `reviews/` preserve their original date and context. Historical implementation counts, source paths, setup commands and operational claims do not establish the current release. Privileged personal operations described there are outside the supported product; their removal from source does not erase history.

- [Charter](HCA_CHARTER_v1.md): original purpose and interface decisions.
- [P19 program](plans/HCA_P19_PRODUCTION_HARDENING_PROGRAM_v1.md), [P20 durable delivery](plans/HCA_P20_DURABLE_DELIVERY_v1.md), [P21a webhook design](plans/HCA_P21A_WEBHOOK_SECURITY_v1.md), [P21b caller keys](plans/HCA_P21B_API_KEY_HARDENING_v1.md), [P23 extension hardening](plans/HCA_P23_EXTENSION_HARDENING_v1.md): historical design context; consult current status before interpreting roadmap completion.
- [Initial deployment](runbooks/HCA_FIRST_DEPLOY.md) and [Chrome Web Store setup](runbooks/HCA_CHROME_WEB_STORE.md): historical setup context requiring reconciliation with current source, identities and privacy policy.
- [Public-readiness baseline](reviews/2026-10-10_public_readiness_review.md): historical review, superseded for current behavior by the remediation records and exact-commit evidence.
- [Interface research](research/UNIVERSAL_INTERFACE_PATTERNS_v1.md): design prior art.
- [September 29 decisions](decisions/DECISIONS_20260929.md), [October 10 decisions](decisions/DECISIONS_20261010.md), [October 11 decisions](decisions/DECISIONS_20261011.md), [October 12 decisions](decisions/DECISIONS_20261012.md): dated decisions; not execution authorization.

## Repository move

The project moved from `human-collections-agent` to `Alternative-Spaces-Inc/HCommAgent` on October 11, 2026. Earlier PR numbers in dated records refer to the previous repository; HCommAgent numbering starts again at 1. Use the source commit and dated evidence when comparing those records.
