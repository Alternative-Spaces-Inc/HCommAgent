# 2026-10-02: HCA-P10, api-key list and revoke

## Why

P9 left one live `smoke-<unix time>` agent key in the database per merge,
with no way to retire it short of a SQL statement. A credential that cannot
be revoked through the API is a gap on its own, smoke or not.

## What this change does

- `GET /v1/api-keys` (admin bearer): every key row, oldest first, as
  prefix, caller, created_at, revoked_at. No secret material: the plaintext
  was shown once at issue and only its hash is stored.
- `POST /v1/api-keys/{prefix}/revoke` (admin bearer): sets `revoked_at`
  once and returns the row; a second call returns the same row unchanged;
  an unknown prefix is 404. `verify_api_key` already refused revoked rows,
  so from the next request every agent route answers 403 for that key.
- `scripts/smoke_golden_path.py`: both modes end by revoking the key they
  minted and asserting the key is then refused. A merge leaves one
  cancelled request and one revoked key row, no live credential.
- `api/openapi.yaml`: both routes and the `ApiKeyView` schema; spec
  validates.

Tests: api 68 (two new: admin-only list and idempotent revoke by prefix,
revoked key refused on an agent route). Smoke locally: 15 of 15 (human)
and 9 of 9 (agent) PASS.

## Red gate

None. No IAM, no secret, no schema migration (`revoked_at` has existed
since the api_keys table). The merge deploys through the trigger and the
`smoke-live` step exercises the new revoke route against the new revision.

## Proof plan

Merge; expect eight steps SUCCESS and the `smoke-live` log to show
`PASS revoke smoke key`, `PASS revoked key refused`, `OK agent path`.

## CodeQL alert 7, dismissed as false positive (Al, 2026-10-02 13:17 PT)

CodeQL `py/weak-sensitive-data-hashing` has flagged `_hash_secret` in
`api/app/apikeys.py` since 2026-09-30 and attached it to this PR because
the new tests pass a plaintext key through that path. The hashed value is
the 24 byte random secret half of an agent key (192 bits of entropy), not a
human password. SHA-256 at rest with a constant time compare is the design
in the plan (section 4 and the API key leakage row of the risk table);
computationally expensive password hashes defend low entropy secrets and
would add latency to every agent request for no gain here. Dismissed
through the GitHub API with this reasoning as the comment.
