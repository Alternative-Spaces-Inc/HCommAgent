"""API key issuance and verification for automation callers.

Format from docs/plans/HCA_CLOUD_RUN_PLAN_v1.md section 4 (Auth and
Cryptography Model): hca_live_<prefix>_<secret>. The prefix is a short,
non-secret string stored in the clear for lookup; the secret half is never
stored, only its keyed hash (key_hash: HMAC-SHA256 under a server pepper
read from HCA_API_KEY_PEPPER), checked with a constant-time comparison so
response timing cannot leak how much of a guess was correct. A stolen table
of hashes is useless without the pepper, which lives in Secret Manager and
never in the database. Rows hashed under the earlier plain SHA-256 scheme
verify once more through the legacy path and are re-hashed under the pepper
on that first use; see docs/decisions/DECISIONS_20261010.md.

This module is new in this sub-task (dispatch hca-p3-20260929); it is not
one of the endpoints the 2026-09-29 decisions log reconciled, so
POST /v1/api-keys is marked PROPOSED in api/openapi.yaml pending the same
review the earlier additions (cancel, inbox, inbox/stream, audit) got.
"""

import hashlib
import hmac
import os
import json
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from . import models

KEY_SCHEME = "hca_live"
PREFIX_BYTES = 6
SECRET_BYTES = 24


HASH_SCHEME = "hmac256"


def _pepper() -> bytes:
    """The server pepper, required. Missing means the deployment is
    misconfigured; refusing to hash is better than hashing under an empty
    key and silently producing a table that verifies against any pepper."""
    value = os.environ.get("HCA_API_KEY_PEPPER", "")
    if len(value) < 16:
        raise RuntimeError("HCA_API_KEY_PEPPER is unset or shorter than 16 characters")
    return value.encode("utf-8")


def _hash_secret(secret: str) -> str:
    """HMAC-SHA256 of the secret half under the server pepper, stored as
    hmac256$<hex>. The secret is server generated (24 random bytes), so a
    slow password KDF buys nothing against guessing; the pepper is what a
    dumped table lacks. Closes CodeQL py/weak-sensitive-data-hashing alert
    11, which flagged the earlier plain SHA-256 on this line."""
    digest = hmac.new(_pepper(), secret.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{HASH_SCHEME}${digest}"


def _legacy_hash_secret(secret: str) -> str:
    """Plain SHA-256, the scheme before the pepper; only used to verify a
    row minted under it once, after which the row is re-hashed."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_api_key(db: Session, caller: str, tenant_id: str = models.DEFAULT_TENANT_ID) -> dict:
    """Mints a new key for caller inside tenant_id, replacing any existing one
    (the api_keys.caller column is unique: one live key per caller, matching
    a single-integration-per-caller model; the caller name is global, the
    key's tenant is not, so re-issuing an existing caller under another
    tenant is refused). Returns the plaintext key once; only its hash is
    persisted. The tenant must exist and not be disabled."""
    tenant = db.get(models.Tenant, tenant_id)
    if tenant is None or tenant.disabled_at is not None:
        raise ValueError("unknown or disabled tenant")
    prefix = secrets.token_hex(PREFIX_BYTES)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    plaintext = f"{KEY_SCHEME}_{prefix}_{secret}"

    existing = db.execute(select(models.ApiKey).where(models.ApiKey.caller == caller)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing:
        if existing.tenant_id != tenant_id:
            raise ValueError("caller already belongs to another tenant")
        existing.key_prefix = prefix
        existing.key_hash = _hash_secret(secret)
        existing.created_at = now
        existing.revoked_at = None
        row = existing
    else:
        row = models.ApiKey(tenant_id=tenant_id, caller=caller, key_prefix=prefix, key_hash=_hash_secret(secret), created_at=now)
        db.add(row)
    db.commit()

    return {"api_key": plaintext, "prefix": prefix, "caller": caller, "tenant_id": row.tenant_id, "created_at": row.created_at}


def verify_api_key(db: Session, presented_key: str) -> Optional[models.ApiKey]:
    """Returns the matching, non-revoked ApiKey row, or None. Looked up by
    the visible prefix (fast, no full-table scan), then confirmed with a
    constant-time hash comparison of the secret half."""
    if not presented_key:
        return None
    parts = presented_key.split("_")
    if len(parts) < 4 or parts[0] != "hca" or parts[1] != "live":
        return None
    prefix = parts[2]
    secret = "_".join(parts[3:])

    row = db.execute(select(models.ApiKey).where(models.ApiKey.key_prefix == prefix)).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if "$" in row.key_hash:
        current = _hash_secret(secret)
        if not hmac.compare_digest(row.key_hash, current):
            previous = json.loads(os.environ.get("HCA_API_KEY_PREVIOUS_PEPPERS", "[]"))
            if not isinstance(previous, list) or len(previous) > 3:
                raise RuntimeError("invalid previous pepper configuration")
            matches = False
            for pepper in previous:
                if not isinstance(pepper, str) or len(pepper) < 16:
                    raise RuntimeError("invalid previous pepper configuration")
                candidate = f"{HASH_SCHEME}$" + hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()
                matches |= hmac.compare_digest(row.key_hash, candidate)
            if not matches:
                return None
            row.key_hash = current
            db.commit()
        return row
    # Legacy row (plain SHA-256, no scheme marker): verify once, re-hash under the pepper.
    if not hmac.compare_digest(row.key_hash, _legacy_hash_secret(secret)):
        return None
    row.key_hash = _hash_secret(secret)
    db.commit()
    return row
