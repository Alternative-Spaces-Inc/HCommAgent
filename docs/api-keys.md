# Caller API keys

Caller keys authenticate an automation as one tenant and caller. The service stores a peppered HMAC-SHA256 hash, not the bearer value. Treat the returned `hca_` key as a secret: it is returned only at issuance or rotation, must not be put in URLs, logs, source control or support messages, and should be held in the automation secret store.

## Scopes and limits

The fixed scopes are `asks:create`, `asks:read`, `asks:cancel` and `webhooks:manage`. Issuance defaults to all four for compatibility. An administrator can set scopes and the per-key `rate_per_minute` and `asks_per_minute` ceilings with `PATCH /v1/api-keys/{prefix}`. The defaults are 600 authenticated calls per minute and 60 ask creations per minute. A tenant may also have `asks_per_day`; zero means unlimited.

A missing scope returns HTTP 403 with detail naming the missing scope and a `WWW-Authenticate` Bearer scope challenge. Exceeding a limit returns HTTP 429 with `Retry-After`, `X-RateLimit-Limit` and `X-RateLimit-Remaining`; successful agent-key responses also carry the rate headers. The first limit refusal in a window records a `limit_hit` event.

`GET /v1/api-keys` reports key lifecycle state (`active`, `rotating`, `expired` or `revoked`), `superseded_by` and `previous_valid_until`. `GET /v1/api-keys/{prefix}/events` reads lifecycle events: `issued`, `scopes_changed`, `limits_changed`, `rotated`, `expired`, `revoked` and `limit_hit`.

## Rotate an automation key

1. Keep the current key working while calling `POST /v1/api-keys/self/rotate` with its Bearer authorization. The response contains the new bearer, its prefix and `previous_valid_until`. Rotation is available to an agent key regardless of its scopes.
2. Store the new bearer directly in the automation's secret store, then switch its traffic to the new value. Never print or persist the bearer in a log.
3. The old key remains valid during the overlap, controlled by `HCA_KEY_ROTATION_OVERLAP_HOURS` (default 24). Confirm the new key is in use, then revoke the old prefix if immediate invalidation is desired. Otherwise it expires at the stated boundary.
4. A rotated or revoked key cannot be rotated again; a repeated attempt returns HTTP 409. Use `GET /v1/api-keys` to verify the old key is `rotating`, with `superseded_by` equal to the new prefix.

An administrator can rotate a specific key with `POST /v1/api-keys/{prefix}/rotate`. The endpoint returns the replacement bearer once. Record only prefixes and event evidence, never the returned secret. Rotation preserves the caller and scopes while resetting the replacement's limits to defaults. See [Operations](OPERATIONS.md) for incident handling.
