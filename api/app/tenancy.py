"""HCA-P19: tenants and their Google owner allowlists.

Routes (platform admin bearer, app/auth.py require_admin_bearer):

  POST   /v1/tenants                      create {slug, name}
  GET    /v1/tenants                      list
  GET    /v1/tenants/{tenant_id}          one tenant with its owner emails
  POST   /v1/tenants/{tenant_id}/disable  stop every key and device of it
  POST   /v1/tenants/{tenant_id}/owners   add {email} to the allowlist
  DELETE /v1/tenants/{tenant_id}/owners/{email}  remove it (kept as a row with removed_at)

Devices, api keys and asks carry tenant_id from the server: a key acts for
its tenant, a device answers for its tenant, and no route accepts a tenant
id from a key or device caller. The default tenant ("default") exists from
the P19 migration and keeps the pre-P19 single owner installation working,
including the HCA_OWNER_EMAILS fallback for Google linking, which applies
to the default tenant only while it has no owner rows.
"""

import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from . import models
from .auth import require_admin_bearer
from .db import get_db
from .json_dates import UTCJSONModel

router = APIRouter(prefix="/v1/tenants", tags=["tenants"])

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
MAX_ACTIVE_OWNERS = max(1, int(os.environ.get("HCA_MAX_ACTIVE_OWNERS", "50")))


def email_is_well_formed(value: str) -> bool:
    """Linear time shape check (no regular expression over user input, which
    CodeQL flagged as polynomial on this file): exactly one @, a non empty
    local part, a domain with a dot that is not at either edge, no
    whitespace, at most 320 characters. Ownership of the address is proven
    by Google at link time, not here."""
    if not value or len(value) > 320 or any(c.isspace() for c in value):
        return False
    local, sep, domain = value.partition("@")
    if not sep or not local or "@" in domain:
        return False
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return False
    return True


class TenantCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=63)
    name: str = Field(min_length=1, max_length=200)


class OwnerAdd(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class TenantView(UTCJSONModel):
    tenant_id: str
    slug: str
    name: str
    created_at: datetime
    disabled_at: Optional[datetime] = None
    owner_emails: list[str] = []


def owner_emails_for(db: Session, tenant_id: str) -> Optional[set]:
    """The tenant's live owner emails; None for the default tenant with no
    rows (so the HCA_OWNER_EMAILS fallback in app/googleauth.py applies).
    Any other tenant with no rows gets an empty set: nobody may link."""
    emails = set(db.execute(select(models.TenantOwner.email).where(
        models.TenantOwner.tenant_id == tenant_id, models.TenantOwner.removed_at.is_(None))).scalars())
    emails = {email.lower() for email in emails}
    # An explicitly emptied allowlist must never resurrect deployment owners.
    configured = db.execute(select(models.TenantOwner.id).where(models.TenantOwner.tenant_id == tenant_id).limit(1)).first()
    if not configured and tenant_id == models.DEFAULT_TENANT_ID:
        return None
    return emails


def _view(db: Session, t: models.Tenant) -> TenantView:
    emails = sorted(owner_emails_for(db, t.id) or set())
    return TenantView(tenant_id=t.id, slug=t.slug, name=t.name, created_at=t.created_at, disabled_at=t.disabled_at, owner_emails=emails)


def _get(db: Session, tenant_id: str) -> models.Tenant:
    t = db.get(models.Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return t


@router.post("", response_model=TenantView, status_code=201)
def create_tenant(body: TenantCreate, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    if not SLUG.match(body.slug):
        raise HTTPException(status_code=422, detail="slug must be 2 to 63 lowercase letters, digits or hyphens, starting with a letter or digit")
    if db.execute(select(models.Tenant).where(models.Tenant.slug == body.slug)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="slug already exists")
    t = models.Tenant(slug=body.slug, name=body.name.strip(), created_at=datetime.now(timezone.utc))
    db.add(t)
    db.commit()
    return _view(db, t)


@router.get("", response_model=list[TenantView], responses={200: {"headers": {"X-HCA-Next-Cursor": {"description": "Pass as after for the next page", "schema": {"type": "string"}}}}})
def list_tenants(response: Response, limit: int = Query(default=200, ge=1, le=500),
                 after: str = Query(default="", max_length=100),
                 db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    rows = db.execute(select(models.Tenant).where(models.Tenant.id > after)
                      .order_by(models.Tenant.id).limit(limit + 1)).scalars().all()
    if len(rows) > limit:
        rows = rows[:limit]
        response.headers["X-HCA-Next-Cursor"] = rows[-1].id
    return [_view(db, t) for t in rows]


@router.get("/{tenant_id}", response_model=TenantView)
def get_tenant(tenant_id: str, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    return _view(db, _get(db, tenant_id))


@router.post("/{tenant_id}/disable", response_model=TenantView)
def disable_tenant(tenant_id: str, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    t = _get(db, tenant_id)
    if t.id == models.DEFAULT_TENANT_ID:
        raise HTTPException(status_code=409, detail="the default tenant cannot be disabled")
    if t.disabled_at is None:
        now = datetime.now(timezone.utc)
        t.disabled_at = now
        for k in db.execute(select(models.ApiKey).where(models.ApiKey.tenant_id == t.id, models.ApiKey.revoked_at.is_(None))).scalars():
            k.revoked_at = now
        for d in db.execute(select(models.Device).where(models.Device.tenant_id == t.id, models.Device.revoked_at.is_(None))).scalars():
            d.revoked_at = now
        db.commit()
    return _view(db, t)


@router.post("/{tenant_id}/owners", response_model=TenantView, status_code=201)
def add_owner(tenant_id: str, body: OwnerAdd, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    t = _get(db, tenant_id)
    email = body.email.strip().lower()
    if not email_is_well_formed(email):
        raise HTTPException(status_code=422, detail="email is not well formed")
    from .requests_api import _lock_tenant_for_quota
    _lock_tenant_for_quota(db, t.id)
    live = db.execute(select(models.TenantOwner).where(
        models.TenantOwner.tenant_id == t.id, models.TenantOwner.email == email, models.TenantOwner.removed_at.is_(None))).scalar_one_or_none()
    if live is None:
        active = db.execute(select(func.count(models.TenantOwner.id)).where(
            models.TenantOwner.tenant_id == t.id, models.TenantOwner.removed_at.is_(None))).scalar_one()
        if active >= MAX_ACTIVE_OWNERS:
            raise HTTPException(status_code=429, detail="tenant active owner limit reached", headers={"Retry-After": "60"})
        db.add(models.TenantOwner(tenant_id=t.id, email=email, created_at=datetime.now(timezone.utc)))
        db.commit()
    return _view(db, t)


@router.delete("/{tenant_id}/owners/{email}", response_model=TenantView)
def remove_owner(tenant_id: str, email: str, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    t = _get(db, tenant_id)
    email = email.strip().lower()
    rows = db.execute(select(models.TenantOwner).where(
        models.TenantOwner.tenant_id == t.id, models.TenantOwner.email == email, models.TenantOwner.removed_at.is_(None))).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="owner not found")
    now = datetime.now(timezone.utc)
    for r in rows:
        r.removed_at = now
    for device in db.execute(select(models.Device).where(
        models.Device.tenant_id == t.id, models.Device.google_email == email,
        models.Device.revoked_at.is_(None))).scalars():
        device.revoked_at = now
    db.commit()
    return _view(db, t)
