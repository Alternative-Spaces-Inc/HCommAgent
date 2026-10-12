# Change log

## 0.3.0 candidate, October 12, 2026

This is a compatible v1-route developer release with stricter validation and authorization. Release tags/assets and a production deployment require their own evidence.

- Resource-bound atomic idempotency, durable ordered events, fenced job leases and answer-time expiry.
- Current owner/device checks, bounded stream revocation, full nonce retention and server-derived audit identity.
- Bounded API reads/input/budgets/registrations and lifecycle export/purge; pending decisions, queued deliveries and live leases are preserved.
- Signed delivery identifiers require receiver header/body agreement and transactional effect deduplication.
- Chrome non-extractable keys, origin/device-scoped durable queue, all six controls and explicit material decisions.
- Mac bounded TCP OAuth, strict concurrency, trusted origins and authorized notification recovery.
- Full generated API contract parity and deliberate drift regression coverage.
- Credential-free local demo, current privacy/security/operations guidance and numeric developer Mac build metadata.

Compatibility: assess existing records/registrations above new bounds; old client identity claims are untrusted metadata, and receivers must verify the signed delivery ID. Preserve current webhook encryption material during a separately validated key migration. Do not rewrite applied migration identifiers. Rebuild/install clients from the reviewed source and verify their checksums; source publication alone does not update installations.

Earlier 0.2.x client versions are historical developer builds. The current supported update path is documented in [Security](SECURITY.md).
