# Repository enforcement settings, verified 2026-10-12

Read through the GitHub REST API on 2026-10-12 (UTC afternoon) against
Alternative-Spaces-Inc/HCommAgent at main 4a4fe08. This record answers review
item 38: the previous public readiness review (2026-10-10) found no branch
protection on the former repository; the settings below are what the public
repository enforces today. Re-verify after any settings change and append a
dated section rather than editing this one.

## Branch protection on main (GET /branches/main/protection)

| Setting | Value |
|---|---|
| Required status checks, strict (branch up to date) | yes |
| Required checks (9) | API Tests (Postgres), Check Bootstrap Manifest Integrity, Check Client Data Hygiene, Check No Dash Characters, Extension Tests (Node 20), Python Dependency Audit (pip-audit), Python Lint (ruff), Swift Tests (macOS), Validate OpenAPI Specification |
| Required approving reviews | 0 (solo maintainer; see exception below) |
| Require code owner review | no |
| Enforce for administrators | yes |
| Required conversation resolution | yes |
| Allow force pushes | no |
| Allow deletions | no |
| Required linear history | no |
| Repository rulesets on main | 0 |

Solo maintainer exception: one person owns the repository and merges every
pull request, so a required approval would block every merge. The compensating
controls are the nine required checks (every merge passes them on an up to
date branch), administrator enforcement (the owner cannot bypass them), and
the pipeline's candidate revision smoke before traffic (docs/runbooks/HCA_DEPLOY_ROLLBACK.md).
When a second maintainer with write access joins, set the approval count to 1
and require code owner review; .github/CODEOWNERS names the owner.

## Code scanning (GET /code-scanning/default-setup)

CodeQL default setup: state configured, query suite extended, languages
actions, javascript-typescript, python, swift. There is no CodeQL workflow
file in .github/workflows by design; CONTRIBUTING.md describes default setup.
Open alerts at verification: 5, all severity warning, rule
py/weak-sensitive-data-hashing on api/app/apikeys.py (HMAC-SHA256 under a
server pepper of a 24 byte random secret; a password KDF adds nothing for a
high entropy server generated secret, the same finding was dismissed as a
false positive on the former repository) and on the two test lines that
exercise the legacy plain SHA-256 rehash path, plus py/overly-permissive-file
on bootstrap/skills/skill_restore.py (chmod 755 of a restored executable
skill; the whole bootstrap tree leaves the product repository under review
item 39). Disposition of these five is an owner selection recorded in
docs/decisions when taken.

## Secret scanning and dependency updates (GET /repos/{owner}/{repo})

| Feature | Status |
|---|---|
| Secret scanning | enabled |
| Push protection | enabled |
| Dependabot security updates | enabled |
| Dependabot version updates | .github/dependabot.yml: pip (api), npm (extension), github-actions, docker |
| Open secret scanning alerts | 0 |
| Open Dependabot alerts | 0 |
| Private vulnerability reporting | enabled (SECURITY.md points at it) |
| Non provider patterns, validity checks | disabled (GitHub Advanced Security features not in use) |

## What this record does not cover

Cloud IAM, Secret Manager access, the Cloud Build trigger's pinned
substitutions and the Cloud Run service configuration are deployment
settings outside GitHub; the pipeline's check-config step verifies the
service configuration on every build and the deploy runbooks describe the
rest. This record is a dated snapshot, not a continuous control.


## Final policy update, October 12, 2026

This section appends the later settings snapshot; the original nine-check and
zero-ruleset record above remains intact as historical evidence. Main was
`a392399` at verification. The root retained the REST protection response and
verified the active ruleset separately.

Branch protection now requires 13 strict, up-to-date status checks, each associated
with GitHub Actions app ID 15368:

- API Tests (Postgres)
- API Tests (SQLite and HTTP)
- Build Configuration and Documentation
- Check Client Data Hygiene
- Check No Dash Characters
- Check Product Isolation
- Container Security and SBOM
- Credential and History Scan
- Extension Tests (Node 24)
- Python Dependency Audit (pip-audit)
- Python Lint (ruff)
- Swift Tests (macOS)
- Validate OpenAPI Specification

Administrator enforcement remains enabled; force pushes and deletions remain
refused, conversation resolution remains required, and the solo-maintainer review
count remains zero. Code-owner review is not required by the current policy.
Source CODEOWNERS identifies the verified maintainer; this is not evidence of an
independent approving review.

Active code-scanning ruleset ID `24952962` has no bypass actors. Its required tool
is CodeQL, with `security_alerts_threshold=high_or_higher` and
`alerts_threshold=errors`. Under [GitHub's merge-protection documentation](https://docs.github.com/en/code-security/concepts/code-scanning/merge-protection),
required analysis still in progress, an unconfigured required tool or findings
meeting the configured threshold can block a pull request. Ruleset merge
protection is separate from status checks. It does not apply to merge queue groups
or Dependabot pull requests analyzed by default setup; all alert-identified lines
must also exist in the pull-request diff. These limitations matter when interpreting
historical or dependency-update findings. [The triage addendum](2026-10-12_security_alert_triage.md)
distinguishes rule severity from security severity; no zero-alert assertion is made.

[PR15](https://github.com/Alternative-Spaces-Inc/HCommAgent/pull/15) source
`9eeadbea6ebcc75c6338bcf9db77a3afd9a42d4f` passed all 13 quality checks in
[run 38306535527](https://github.com/Alternative-Spaces-Inc/HCommAgent/actions/runs/38306535527).
CodeQL Swift analysis remains in progress at this update; inspect [the actual analyses](https://github.com/Alternative-Spaces-Inc/HCommAgent/pull/15/checks)
before describing all security checks as complete. Later source/image/tool changes
need renewed exact-head evidence.

The deployment substitution `_APPROVED_COMMIT` defaults empty. A main merge may
start Cloud Build, but the first guard refuses cloud mutation without an exact
40-character approved source commit matching that build. These source/settings
changes did not provision keys or perform a production rollout. Final release
tag/artifact publication remains separate.
