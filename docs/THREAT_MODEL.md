# Threat model

This document describes the implemented owner-authorized scope as of the October 12, 2026 remediation work. It is a review aid, not a certification or proof that a deployed service uses this source/configuration. Read it with [Architecture](ARCHITECTURE.md), [Operations](OPERATIONS.md) and the [current status](STATUS.md).

## Assets and trusted parties

Protect decision integrity, tenant confidentiality, recorded responder identity, credentials/private signing keys, webhook effects, durable history and service availability. The API/database administrator, deployment identity, user's browser/OS and Google verification service are trusted within their respective roles. An agent is authenticated but its titles, suggestions, commands, paths and supporting data remain untrusted content. A registered receiver is trusted to perform only the intended effect and enforce its own deduplication.

The service cannot force an external agent to stop, prevent an administrator from editing a database, establish that a proposed command is safe, or prove an external action happened merely because a webhook was accepted. Tenant owners authorize decisions. Arbitrary departments/roles, customer identities and enterprise identity-provider policies are not implemented by the descriptive `audience` field.

## Boundaries and defenses

| Boundary/threat | Current defense | Residual assumption or evidence needed |
| --- | --- | --- |
| Unauthenticated agent or cross-caller access | Random caller keys, peppered HMAC hashes, authenticated tenant/caller queries | Operator protects key issuance/admin token; key possession grants that caller's scope |
| Removed owner or revoked device continuing to act | Current membership/device/tenant checks; timed stream rechecks | Revocation depends on server/database availability and the configured interval |
| Forged/replayed human request | P-256 signature, method/path/body binding, timestamp window, shared durable nonce retention | Correct clocks; device/browser/OS code is trusted; local compromise may use a key without exporting it |
| Client-supplied audit identity | Server derives owner, device, role and surface | Optional path/duration/content are untrusted telemetry, not verified behavior |
| Retry collision or crash after partial state | Resource-bound key reservation and atomic state/audit/event/outbox/retry response | Retry lifetime is bounded; external consumers still need durable effect deduplication |
| Missed stream event or late commit | Durable transaction events, ordered Postgres insertion, bounded replay/reset, inbox polling | Presentation may be delayed; global event serialization needs load evidence |
| Stale worker after lease takeover | Renewal, near-execution claim and generation-fenced mutations | Network effects cannot be rolled back by losing a lease |
| Slow or malicious webhook host | Registered destination, HTTPS/SSRF checks, checked address pinning, no redirects, total deadline and bounded response | DNS/network infrastructure availability; receiver may acknowledge without performing a business action |
| Captured webhook with substituted dedupe ID | ID inside signed body plus header equality; freshness and constant-time HMAC comparison | Receiver verifies raw bytes and deduplicates the effect transactionally |
| Browser offline queue/key races | Atomic IndexedDB initialization, non-extractable key, single background writer, scoped/bounded retry queue | Browser update and OS compromise remain trusted-local risks; Chrome may suspend work |
| Old-server decisions submitted to a new host | Strict origin, deliberate host change, scoped state, request abortion and stale-popup rejection | User verifies the chosen host; already delivered old-server requests cannot be recalled |
| OAuth callback injection or parser crash | State before success/error, duplicate-field rejection, bounded fragmented TCP reads/deadlines | Owner-reported manual confirmation is recorded separately; different deployments/platforms need their own evidence |
| Payload/storage/connection exhaustion | Body/schema limits, shared actor/tenant budgets, per key rate_per_minute and asks_per_minute ceilings plus an optional tenant asks_per_day cap (429 with Retry-After, X-RateLimit headers, one limit_hit event per key per window), active-request/page/queue caps | Edge unauthenticated traffic, global stream/instance caps, storage budgets and load alerts need operator controls |
| Credential leaked through a URL (access logs, referrers, browser history) | The outermost ASGI guard refuses any request carrying a query parameter named access_token, api_key, apikey, token, key or secret with 400 before routing or logging; Google tokeninfo is called with the token in a POST form body, never in the query | Proxies and edge logs outside the service may still record full URLs of refused requests; header-borne credentials remain trusted to TLS |
| Dependency/build/release compromise | Hashed locks, pinned references, scans/SBOM, protected review path where enabled | Scanners are incomplete; branch/settings and release identity must be checked on the actual revision |
| Notification or stored-data disclosure | Redacted previews by default, local scoped state, explicit export/purge and transient cleanup | Users can enable previews; backups/logs/profiles/system notifications need operator/user retention |

## Configuration and secrets

`HCA_ENV=production` requires Postgres, strong separate admin/sweep credentials, OAuth client audiences and the Google owner gate, and refuses the insecure webhook development switch. Development deliberately permits disposable local credentials and a loopback mock receiver; it is not a production bypass.

Webhook secret encryption supports an optional versioned keyring separate from the agent-key pepper, with legacy dual-read compatibility. Deploy compatible readers everywhere before enabling new writes. Retain decrypt keys until every retained destination and backup that needs them has a defined migration/expiry path. Pepper overlap is a temporary verification migration, not a way to skip rotating compromised caller credentials. The root/admin credential remains a high-value platform secret; it is not tenant-owner authorization. See the rotation and incident procedures in [Operations](OPERATIONS.md).

## Assurance limits

Regression tests target the reproduced defects and related concurrency/failure boundaries. A clean dependency scan does not prove absence of vulnerabilities. A full generated OpenAPI comparison proves contract/source agreement, not that every possible runtime behavior or authorization policy is correct. SQLite tests do not substitute for Postgres locks/commit ordering. Synthetic Chromium fixtures do not establish Chrome OAuth/store readiness. Owner-reported Google/native/accessibility validation is recorded in the dated approval document; it is not an independent production test. Mac logic/loopback checks do not establish public distribution readiness.

No production penetration test, formal regulatory mapping, restored-backup drill, live load/soak result or cost/SLO claim is supplied by this update. Third-party connector roadmaps and privileged personal-agent tools are outside the product scope. A private operations repository has not been created by removing those files; their Git history still exists and requires separate publication/credential review.

Repository scan execution and finding resolution are separate. The [dated triage addendum](reviews/2026-10-12_security_alert_triage.md) distinguishes CodeQL rule severity from security severity and records existing findings without treating a passing analysis as zero alerts.
