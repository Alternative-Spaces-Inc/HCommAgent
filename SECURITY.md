# Security policy

HCA is an owner-authorized decision service. Its current trust model, limits and unresolved operational evidence are described in [Threat model](docs/THREAT_MODEL.md), [Operations](docs/OPERATIONS.md) and the [remediation record](docs/SECURITY_REMEDIATION.md). No generic security or regulatory certification is claimed.

## Reporting

Report suspected vulnerabilities through [GitHub Private Vulnerability Reporting](https://github.com/Alternative-Spaces-Inc/HCommAgent/security/advisories/new), not public issues. Include the affected revision, a minimal reproduction using synthetic data, expected/observed behavior and impact. Do not include live secrets, personal decisions or another tenant's data.

The maintainer aims to acknowledge reports within three business days and triage within seven. These are response targets, not a staffed support guarantee. Disclosure is coordinated privately; advisories should identify fixed source revisions, affected artifacts, deployment requirements and any credential rotation needed. Client updates and server rollout are separate actions.

## Supported update path

Security fixes are maintained on `main` and reviewed branches intended for merge. There is no promise of backports to older source tags or previously installed developer artifacts. Pre-1.0 status does not exempt the current clients from security maintenance. Operators should build or install artifacts containing the documented fix, check their revision/checksum and deploy the compatible API changes.

| Component | Supported path |
| --- | --- |
| API | Current reviewed source and migrations, production configuration, operator-controlled rollout |
| Chrome | Current source/package on Chrome 122+; verify extension ID and Google OAuth configuration |
| macOS | Current developer source on macOS 14+; rebuild locally and preserve/re-register device identity as documented |
| Public Mac distribution | Not provided; Developer ID, notarization and update service would require a separate release process |

Publishing a branch does not update an already installed extension, Mac bundle or production server. If an advisory affects deployed data or secrets, operators must follow the incident steps even after a code fix.

## Enforced boundaries

Agent credentials are random secrets stored as HMAC-SHA256 hashes under a server-side pepper. Verification uses constant-time comparison. Device requests use P-256 signatures over the timestamp, nonce, method, path/query and body hash; accepted nonces are retained long enough to cover the entire timestamp acceptance window. Chrome private keys are non-extractable IndexedDB objects; Mac private keys use the login Keychain. Local browser/OS compromise remains outside the protection those mechanisms provide.

Production requires explicit `HCA_ENV=production`, Postgres, strong separate admin/sweep credentials, configured Google OAuth audiences, and the Google link gate. Owner membership is checked against current server state on protected calls and periodically on streams. An empty configured owner list grants nobody access. The tenant and caller come from authenticated credentials; `audience` and client descriptions do not confer authorization. Audit owner/device/role/surface identity is derived by the server, while optional client context remains untrusted metadata.

Idempotency, state transitions, audit, durable events and webhook outbox writes are transactional. Job results are fenced by lease ownership/generation. Receivers authenticate the signed delivery identifier and deduplicate external effects; at-least-once delivery can produce repeated requests. See [webhooks](docs/webhooks.md) for the exact verification contract and bounded retries.

## Scope and limits

API authorization, client signing/storage, migrations, pipeline configuration and HCA's use of dependencies are in scope for reports. Upstream or hosting defects are coordinated with their maintainers when relevant. HCA does not control an agent's behavior outside this API, a webhook receiver's effects, a compromised administrator, Google, the user's operating system or a cloud operator's IAM/backups.

Repository credential/history scanning, dependency audits, CodeQL and container checks reduce specific risks. Their coverage and results must be checked on the reviewed revision. Live repository settings are recorded in [dated evidence](docs/REPOSITORY_SETTINGS_2026-10-12.json); production IAM, restored backups, load limits and real-client Google authentication need operator evidence. The current branch is a remediation candidate, not proof that production has been updated.

The served `/privacy` page describes Google access-token verification, decision data, webhook recipients, local caches and operator-controlled retention/export/deletion. Deployments must provide their actual operator contact and backup/log retention. The [Apache-2.0 license](LICENSE) covers this repository; contributors must also preserve upstream notices and publication rights for added code/assets.
