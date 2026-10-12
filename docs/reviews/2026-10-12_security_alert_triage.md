# Security alert review: October 12, 2026

This is a source and configuration review, not a certification or proof that
every possible vulnerability has been found. Automated results are reported
separately from the owner's recorded manual validation.

## Code scanning

The GitHub API reported five open CodeQL alerts at this review. Their rule
severity is `warning`; their security severity is `high`. The earlier
[enforcement snapshot](2026-10-12_enforcement_verification.md) reports rule
severity and must not be read as reducing the security classification.

| Locations | Review and disposition |
| --- | --- |
| Two API-key hashing locations in `api/app/apikeys.py` | The issuance route generates the secret with `secrets.token_urlsafe(24)`, providing 192 bits of randomness. Callers cannot select a low-entropy password as an issued secret. Current storage uses HMAC-SHA256 with a separately held pepper; the SHA-256 compatibility path verifies old random API keys and upgrades them on successful use. A password KDF is appropriate for human passwords; these are server-generated random bearer credentials. This contextual assessment supports a false-positive disposition for the weak-password-hashing rule. Preserve constant-time verification, entropy and revocation/rotation regressions. |
| Two legacy hashing lines in `api/tests/test_apikeys.py` | Synthetic fixtures exercise compatibility and successful upgrade; they do not store production credentials. Keep the regression rather than suppressing its execution. |
| `bootstrap/skills/skill_restore.py` file permissions | Privileged personal-agent operations have been removed from the product tree and preserved as a local reference outside it. This removes the affected executable from the candidate product source. Historical Git commits remain available; no history rewrite or claim of historical erasure is made. The reference workflow has not been activated or certified as an approval boundary. |

On October 12, the documented review was applied in GitHub: alerts 1 and 2
were dismissed as false positives, and alerts 3 and 4 as synthetic test use,
each with its reason recorded. The new previous-pepper HMAC finding (alert 6)
was independently reviewed and dismissed as a false positive for the same
server-issued random-credential reason; bounded overlap and CAS upgrade
regressions remain enabled. Alert 5 remained open pending removal from
the default branch. No zero-alert claim is made. A successful CodeQL job
means analysis completed; it does not itself establish that findings were resolved.

## Credential scan

Gitleaks 8.30.1 scans the current source snapshot and every fetched Git ref.
CI fetches complete history and rejects shallow clones. The official binary
is pinned by version and release-archive SHA-256, with default rules plus HCA
and Google access-token patterns. Output is fully redacted.

The initial scan reported 20 historical matches and seven current matches.
Review identified synthetic test peppers, the published signature-test key,
the public Chrome extension identity key, a Swift key-type declaration and
historical mock-token examples. The checked-in exception record binds each
finding to its fingerprint and the SHA-256 of the complete file. A changed
file or a new finding requires review; directories, test suites and credential
classes are not broadly excluded. New and deleted-credential regressions
exercise failure and redaction. This is pattern-based scanning, not a claim
that secret detection is exhaustive.

## Deployment and operator evidence

Repository changes bind configuration checks, smoke and traffic promotion to
one immutable image and exact candidate revision. Optimistic concurrency
refuses stale promotion. Separate migration credentials, bounded migration
locks and explicit recovery are implemented in source; actual cloud IAM,
database grants, secret values, rollback, recovery and load results require
operator verification. Those live actions were not performed by the local
tests. Existing webhook ciphertext remains readable during the documented
optional key transition; dropping old material before its migration is unsafe.

The [manual approval](2026-10-12_manual_validation_approval.md) is an
owner-reported confirmation of the original client validation. New repository
integration is supported by its independently reported automated evidence.
