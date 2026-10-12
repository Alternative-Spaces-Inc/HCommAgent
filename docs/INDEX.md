# Documentation index

Start with the current documents below. Dated plans, dispatch notes, reviews and deployment records preserve design history; their claims apply to their recorded revision/date and must not be treated as authorization to run an operational workflow.

## Current review path

- [README](../README.md): runnable scope, five-minute demo, clients and validation limits.
- [Portfolio case study](PORTFOLIO.md): business workflow, engineering choices, demonstration and attribution.
- [Architecture](ARCHITECTURE.md): transaction boundaries, state, authorization, delivery, limits and source map.
- [Threat model](THREAT_MODEL.md): trust assumptions, defenses and residual risks.
- [Operations](OPERATIONS.md): production prerequisites, staged rollout, credentials, incident response and recovery evidence.
- [Security remediation](SECURITY_REMEDIATION.md): all 43 review items, implementation/evidence and follow-up requirements.
- [Repository settings evidence](REPOSITORY_SETTINGS_2026-10-12.json): dated live GitHub inspection, separate from source implementation.
- [Webhook receiver contract](webhooks.md): registration, signed delivery-ID verification, deduplication and retries.
- [Contributing](../CONTRIBUTING.md): supported tools, dependency locks, executable checks and contract updates.
- [Security policy](../SECURITY.md): private reporting and supported update path.
- [Code of Conduct](../CODE_OF_CONDUCT.md): community conduct and reporting.
- [Chrome client](../extension/README.md): all six controls, key migration/rotation, queue bounds and browser checks.
- [Mac developer client](../macos/README.md): two controls, OAuth, local signing, upgrades and platform checks.
- [Generated API contract](../api/openapi.yaml): reviewed schemas, route security and limits.

## Historical material

- [Charter](HCA_CHARTER_v1.md): original product direction; roadmap features are not current capabilities.
- [Decisions](decisions/): dated alternatives and choices.
- [Plans](plans/): phase plans and prior work dispatches, including broader future features.
- [Deploy records](deploys/): historical revisions and observed checks; they do not prove the remediation candidate is deployed.
- [Earlier public-readiness review](reviews/2026-10-10_public_readiness_review.md): historical baseline, superseded for current status by the remediation matrix and exact-revision checks.
- [First deployment runbook](runbooks/HCA_FIRST_DEPLOY.md): historical cloud setup detail; use the current production prerequisites in Operations and review the pipeline before deployment.
- [Chrome Web Store runbook](runbooks/HCA_CHROME_WEB_STORE.md): store setup context; verify current OAuth IDs, permissions, privacy wording and package version before submission.
- [Research](research/): reference material for interaction design.

Privileged `bootstrap/`, `platform/` and `agents/` content is no longer part of the product tree. The operations-isolation gate enforces that separation. Removal does not erase historical commits or create a new private operations repository.

The project moved to `Alternative-Spaces-Inc/HCommAgent` on October 11, 2026. Older records can refer to pull-request numbers in the former repository; use commit identity and the recorded context instead of assuming those numbers identify current public PRs.

## Remediation evidence

- [SECURITY_REMEDIATION.md](SECURITY_REMEDIATION.md): review closure and dated repository settings.
- [REPOSITORY_SETTINGS_2026-10-12.json](REPOSITORY_SETTINGS_2026-10-12.json): review closure and dated repository settings.
