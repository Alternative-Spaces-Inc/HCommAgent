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

The published POST /v1/api-keys route requires the platform administrator
bearer. New secrets contain 24 random bytes (192 bits). An explicit bounded
previous-pepper list permits a transition; successful verification upgrades
the hash without overwriting a concurrent credential rotation or revocation.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, func, update

from . import models

KEY_SCHEME = "hca_live"
PREFIX_BYTES = 6
SECRET_BYTES = 24
MAX_ACTIVE_API_KEYS = max(1, int(os.environ.get("HCA_MAX_ACTIVE_API_KEYS", "1000")))


HASH_SCHEME = "hmac256"
MAX_PREVIOUS_PEPPERS = 3

# HCA-P21b decision 9: a fixed vocabulary of four scopes. A key carries any
# subset; omitted at issuance means all four so pre-P21b callers and the
# smoke script keep working. Agent routes declare the scope they need via
# auth.require_scope and answer 403 naming the missing scope.
SCOPES = ("asks:create", "asks:read", "asks:cancel", "webhooks:manage")


def normalize_scopes(value) -> list[str]:
    """Validates a requested scope list: None means every scope; otherwise a
    non-empty list of distinct known names, returned in vocabulary order."""
    if value is None:
        return list(SCOPES)
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("scopes must be a non-empty list")
    seen = []
    for item in value:
        if not isinstance(item, str) or item not in SCOPES:
            raise ValueError(f"unknown scope {item!r}; known scopes: {', '.join(SCOPES)}")
        if item in seen:
            raise ValueError(f"duplicate scope {item!r}")
        seen.append(item)
    return [s for s in SCOPES if s in seen]


def record_key_event(db: Session, row: models.ApiKey, action: str, actor: str, detail: Optional[dict] = None,
                     at: Optional[datetime] = None) -> models.ApiKeyEvent:
    """Adds an api_key_events row to the current transaction (the caller
    commits), so the event and the change land together or not at all."""
    event = models.ApiKeyEvent(tenant_id=row.tenant_id, api_key_id=row.id, action=action, actor=actor,
                               detail=json.dumps(detail, sort_keys=True) if detail is not None else None,
                               at=at or datetime.now(timezone.utc))
    db.add(event)
    return event


def previous_peppers(env=None) -> list[str]:
    """Explicit, bounded verification/decryption overlap; never log values."""
    env = os.environ if env is None else env
    try:
        raw = env.get("HCA_API_KEY_PREVIOUS_PEPPERS", "[]")
        if not isinstance(raw, str) or len(raw) > 16384:
            raise ValueError()
        peppers = json.loads(raw)
        if not isinstance(peppers, list) or len(peppers) > MAX_PREVIOUS_PEPPERS:
            raise ValueError()
        if any(not isinstance(value, str) or not 16 <= len(value) <= 4096 for value in peppers):
            raise ValueError()
        if len(set(peppers)) != len(peppers):
            raise ValueError()
        return peppers
    except (ValueError, TypeError):
        raise RuntimeError("invalid previous API-key pepper configuration") from None


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
    dumped table lacks."""
    digest = hmac.new(_pepper(), secret.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{HASH_SCHEME}${digest}"


def _legacy_hash_secret(secret: str) -> str:
    """Plain SHA-256, the scheme before the pepper; only used to verify a
    row minted under it once, after which the row is re-hashed."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_api_key(db: Session, caller: str, tenant_id: str = models.DEFAULT_TENANT_ID, scopes=None,
                  rate_per_minute=None, asks_per_minute=None) -> dict:
    """Mints a new key for caller inside tenant_id, replacing any existing one
    (the api_keys.caller column is unique: one live key per caller, matching
    a single-integration-per-caller model; the caller name is global, the
    key's tenant is not, so re-issuing an existing caller under another
    tenant is refused). Returns the plaintext key once; only its hash is
    persisted. The tenant must exist and not be disabled."""
    tenant = db.get(models.Tenant, tenant_id)
    if tenant is None or tenant.disabled_at is not None:
        raise ValueError("unknown or disabled tenant")
    from .requests_api import _lock_tenant_for_quota
    _lock_tenant_for_quota(db, tenant_id)
    prefix = secrets.token_hex(PREFIX_BYTES)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    plaintext = f"{KEY_SCHEME}_{prefix}_{secret}"

    existing = db.execute(select(models.ApiKey).where(models.ApiKey.caller == caller)).scalar_one_or_none()
    if existing is None or existing.revoked_at is not None:
        active = db.execute(select(func.count(models.ApiKey.id)).where(
            models.ApiKey.tenant_id == tenant_id, models.ApiKey.revoked_at.is_(None))).scalar_one()
        if active >= MAX_ACTIVE_API_KEYS:
            raise HTTPException(status_code=429, detail="tenant active API key limit reached", headers={"Retry-After": "60"})
    scope_list = normalize_scopes(scopes)
    scopes_json = json.dumps(scope_list)
    now = datetime.now(timezone.utc)
    if existing:
        if existing.tenant_id != tenant_id:
            raise ValueError("caller already belongs to another tenant")
        existing.key_prefix = prefix
        existing.key_hash = _hash_secret(secret)
        existing.created_at = now
        existing.revoked_at = None
        existing.scopes = scopes_json
        existing.rate_per_minute = rate_per_minute
        existing.asks_per_minute = asks_per_minute
        row = existing
    else:
        row = models.ApiKey(tenant_id=tenant_id, caller=caller, key_prefix=prefix, key_hash=_hash_secret(secret),
                            created_at=now, scopes=scopes_json, rate_per_minute=rate_per_minute, asks_per_minute=asks_per_minute)
        db.add(row)
        db.flush()
    from .limits import key_asks_ceiling, key_rate_ceiling
    record_key_event(db, row, "issued", "admin", {"scopes": scope_list, "prefix": prefix,
                                                  "rate_per_minute": key_rate_ceiling(row), "asks_per_minute": key_asks_ceiling(row)}, at=now)
    db.commit()

    return {"api_key": plaintext, "prefix": prefix, "caller": caller, "tenant_id": row.tenant_id, "created_at": row.created_at,
            "scopes": scope_list, "rate_per_minute": key_rate_ceiling(row), "asks_per_minute": key_asks_ceiling(row)}


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
    stored = row.key_hash
    current = _hash_secret(secret)
    if re.fullmatch(r"hmac256\$[0-9a-f]{64}", stored):
        if hmac.compare_digest(stored, current):
            return row
        matches = False
        for pepper in previous_peppers():
            candidate = f"{HASH_SCHEME}$" + hmac.new(pepper.encode("utf-8"), secret.encode("utf-8"), hashlib.sha256).hexdigest()
            matches |= hmac.compare_digest(stored, candidate)
        if not matches:
            return None
    elif not re.fullmatch(r"[0-9a-f]{64}", stored) or not hmac.compare_digest(stored, _legacy_hash_secret(secret)):
        return None
    # Upgrade only the credential that was verified. A concurrent rotation
    # or revocation must not be overwritten by this successful old-key use.
    prefix = row.key_prefix
    credential_id = row.id
    changed = db.execute(update(models.ApiKey).where(models.ApiKey.id == row.id,
        models.ApiKey.key_prefix == prefix, models.ApiKey.key_hash == stored,
        models.ApiKey.revoked_at.is_(None)).values(key_hash=current)
        .execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        db.rollback()
        # Another valid request can win the same pepper upgrade. Refresh
        # its result; rotation/revocation or a different secret still denies.
        current_row = db.execute(select(models.ApiKey).where(models.ApiKey.id == credential_id,
            models.ApiKey.key_prefix == prefix).execution_options(populate_existing=True)).scalar_one_or_none()
        if current_row is not None and current_row.revoked_at is None and hmac.compare_digest(current_row.key_hash, current):
            return current_row
        return None
    db.commit()
    db.refresh(row)
    if row.revoked_at is not None or row.key_prefix != prefix or row.key_hash != current:
        return None
    return row
