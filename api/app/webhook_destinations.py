"""HCA-P21a: the webhook destination registry (decision 7).

A caller registers a URL once under its api key and receives the signing
secret exactly once. Asks may then name that URL in deliver_to; any other
URL is rejected with 400 at ask creation (requests_api.py). The URL passes
the SSRF rules (app/ssrf.py) here and again at every send.

Secrets: whsec_<43 url safe chars> (32 random bytes). Stored as
hmac256$<hex> under the api key pepper, the same scheme as api keys, so a
dumped table is useless without the pepper. Rotation returns a new secret
once and keeps the previous hash valid for ROTATION_OVERLAP so a receiver
can switch; during the overlap the sender signs with both (Day 2).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from . import models, ssrf, webhook_crypto
from .apikeys import _hash_secret

SECRET_PREFIX = "whsec_"
ROTATION_OVERLAP = timedelta(hours=24)
MAX_ACTIVE_PER_CALLER = 20


class RegistryError(ValueError):
    status_code = 400


class NotFound(RegistryError):
    status_code = 404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def _audit(db: Session, dest: models.WebhookDestination, action: str, actor: str, detail: Optional[dict], now: datetime) -> None:
    db.add(models.WebhookDestinationEvent(tenant_id=dest.tenant_id, destination_id=dest.id, action=action,
                                          actor=actor, detail=detail, at=_naive(now)))


def active_for(db: Session, tenant_id: str, caller: str) -> List[models.WebhookDestination]:
    return list(db.execute(select(models.WebhookDestination).where(
        models.WebhookDestination.tenant_id == tenant_id,
        models.WebhookDestination.caller == caller,
        models.WebhookDestination.revoked_at.is_(None),
    ).order_by(models.WebhookDestination.created_at)).scalars().all())


def all_for(db: Session, tenant_id: str, caller: str) -> List[models.WebhookDestination]:
    return list(db.execute(select(models.WebhookDestination).where(
        models.WebhookDestination.tenant_id == tenant_id,
        models.WebhookDestination.caller == caller,
    ).order_by(models.WebhookDestination.created_at)).scalars().all())


def find_active_by_url(db: Session, tenant_id: str, caller: str, url: str) -> Optional[models.WebhookDestination]:
    """The destination an ask's deliver_to.url names, or None (400 upstream)."""
    for dest in active_for(db, tenant_id, caller):
        if dest.url == url:
            return dest
    return None


def get_own(db: Session, tenant_id: str, caller: str, destination_id: str) -> models.WebhookDestination:
    dest = db.get(models.WebhookDestination, destination_id)
    if dest is None or dest.tenant_id != tenant_id or dest.caller != caller:
        raise NotFound("webhook destination not found")
    return dest


def register(db: Session, *, tenant_id: str, caller: str, url: str, now: Optional[datetime] = None) -> tuple[models.WebhookDestination, str]:
    """Check the URL, store the destination, return (row, secret). The
    secret is not retrievable afterwards. Re-registering an active URL is a
    400 (rotate instead); a revoked URL may be registered again."""
    now = now or _now()
    try:
        checked = ssrf.check_url(url)
    except ssrf.DestinationRejected as exc:
        raise RegistryError(f"url rejected: {exc}") from None
    from .requests_api import _lock_tenant_for_quota
    _lock_tenant_for_quota(db, tenant_id)
    if find_active_by_url(db, tenant_id, caller, url) is not None:
        raise RegistryError("url is already registered; rotate its secret or revoke it first")
    active = db.execute(select(func.count(models.WebhookDestination.id)).where(
        models.WebhookDestination.tenant_id == tenant_id, models.WebhookDestination.caller == caller,
        models.WebhookDestination.revoked_at.is_(None))).scalar_one()
    if active >= MAX_ACTIVE_PER_CALLER:
        raise RegistryError(f"caller already has {MAX_ACTIVE_PER_CALLER} active destinations")
    secret = _new_secret()
    dest = models.WebhookDestination(
        id="whd_" + secrets.token_hex(12), tenant_id=tenant_id, caller=caller, url=url,
        secret_prefix=secret[:len(SECRET_PREFIX) + 8], secret_hash=_hash_secret(secret),
        pinned_addresses=checked.addresses, created_at=_naive(now),
    )
    dest.secret_enc = webhook_crypto.encrypt(secret, destination_id=dest.id)
    db.add(dest)
    db.flush()
    _audit(db, dest, "registered", caller, {"url": url, "addresses": checked.addresses}, now)
    db.commit()
    db.refresh(dest)
    return dest, secret


def rotate(db: Session, *, tenant_id: str, caller: str, destination_id: str, now: Optional[datetime] = None) -> tuple[models.WebhookDestination, str]:
    now = now or _now()
    dest = get_own(db, tenant_id, caller, destination_id)
    if dest.revoked_at is not None:
        raise RegistryError("webhook destination is revoked")
    secret = _new_secret()
    dest.previous_secret_hash = dest.secret_hash
    dest.previous_secret_enc = dest.secret_enc
    dest.previous_valid_until = _naive(now + ROTATION_OVERLAP)
    dest.secret_hash = _hash_secret(secret)
    dest.secret_enc = webhook_crypto.encrypt(secret, destination_id=dest.id)
    dest.secret_prefix = secret[:len(SECRET_PREFIX) + 8]
    dest.rotated_at = _naive(now)
    _audit(db, dest, "rotated", caller, {"previous_valid_until": dest.previous_valid_until.isoformat() + "Z"}, now)
    db.commit()
    db.refresh(dest)
    return dest, secret


def revoke(db: Session, *, tenant_id: str, caller: str, destination_id: str, now: Optional[datetime] = None) -> models.WebhookDestination:
    now = now or _now()
    dest = get_own(db, tenant_id, caller, destination_id)
    if dest.revoked_at is None:
        dest.revoked_at = _naive(now)
        _audit(db, dest, "revoked", caller, None, now)
        db.commit()
        db.refresh(dest)
    return dest


def current_secret_hashes(dest: models.WebhookDestination, now: Optional[datetime] = None) -> List[str]:
    """Hashes a sender may sign under right now: the current one, plus the
    previous one while its overlap lasts (Day 2 signs with both)."""
    now = _naive(now or _now())
    out = [dest.secret_hash]
    if dest.previous_secret_hash and dest.previous_valid_until and dest.previous_valid_until > now:
        out.append(dest.previous_secret_hash)
    return out


def signing_secrets(dest: models.WebhookDestination, now: Optional[datetime] = None) -> List[str]:
    """The plaintext secrets a delivery is signed under right now (decision
    8): the current one, plus the previous one while its overlap lasts.
    Empty when the row predates Day 2 and holds a hash only; the caller must
    rotate the destination to get signing material."""
    now = _naive(now or _now())
    out: List[str] = []
    try:
        out.append(webhook_crypto.decrypt(dest.secret_enc or "", destination_id=dest.id))
    except webhook_crypto.SecretUnreadable:
        return []
    if dest.previous_secret_enc and dest.previous_valid_until and dest.previous_valid_until > now:
        try:
            out.append(webhook_crypto.decrypt(dest.previous_secret_enc, destination_id=dest.id))
        except webhook_crypto.SecretUnreadable:
            pass
    return out
