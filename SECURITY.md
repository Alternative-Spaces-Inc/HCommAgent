# Security Policy

Security policy for HCommAgent, a human control plane for agentic retail, marketing, merchandising and creative operations.

## Supported Versions

Only the `main` branch receives security updates. Previous releases or feature branches are not supported.

| Version | Supported |
| --- | --- |
| main | Yes |
| < 1.0 | No |

## Reporting a Vulnerability

Do not report security vulnerabilities through public GitHub issues.

Please report vulnerabilities via GitHub Private Vulnerability Reporting using the form at:
https://github.com/Alternative-Spaces-Inc/HCommAgent/security/advisories/new

### Response Targets

- Response acknowledgment: within 3 business days
- Vulnerability triage: within 7 business days

## Scope

In scope:
- api
- extension
- macos
- cloudbuild

Out of scope:
- Third-party services and upstream dependencies

## Security Model

- API keys hashed at rest: API keys are stored solely as SHA256 hashes and checked using constant-time comparison.
- Device P-256 signatures with timestamp window: Extension and client requests sign requests with ECDSA P-256 keys, enforced with timestamp freshness validation.
- Google account link gate: Device access can require linking a verified Google account matching allowed tenant owner emails.
- Tenant scoping: Every record, event stream, and request is strictly scoped by tenant identifier.
- Admin bearer for platform routes: Administrative platform operations require a dedicated admin bearer token.

## Disclosure Policy

Vulnerability details are coordinated privately until a fix is deployed to main. Public disclosures are published through GitHub Security Advisories.
