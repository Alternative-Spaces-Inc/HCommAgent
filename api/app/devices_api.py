"""Device registration for the human surface (HCA-P6, app/devicesig.py).

POST /v1/devices        admin bearer; body {label, public_key_pem, surface?}
GET  /v1/devices        admin bearer; every device, revoked included
POST /v1/devices/{id}/revoke  admin bearer
PATCH /v1/devices/{id}  admin bearer; body {priority} (HCA-P14, app/routing.py)
PATCH /v1/devices/self  device signature; body {priority}: the owner sets this
                        device's ladder place from the Mac menu or the
                        extension options page (HCA-P14 priority controls)
GET  /v1/devices/self              device signature; the calling device's own view
POST /v1/devices/self/google-link  device signature; body {access_token} (HCA-P7)

The owner runs registration once per browser profile with the PEM the
extension's options page shows. Registering the same key again is
idempotent (200 with the existing row, label updated); a revoked key
cannot be re-registered by posting it again (409), a new key is needed.

Google link (HCA-P7, app/googleauth.py): the options page signs in with
chrome.identity and posts the access token over a device-signed call. The
server verifies the token against the Chrome Extension OAuth client and
the owner list and records sub, email and the time on the device row.
Linking again replaces the account; linking a device to an account that
another active device already holds is allowed (one owner, several
browser profiles). Each decision snapshots the verified Google subject/email and device ID;
client descriptions are stored separately as untrusted metadata.
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from . import models
from .auth import require_admin_bearer, require_device, require_linked_device, require_current_device
from . import googleauth
from . import events
from . import tenancy
from .db import get_db
from .devicesig import device_id_for_public_key_pem
from .schemas import Surface
from .json_dates import UTCJSONModel

router = APIRouter(prefix="/v1/devices", tags=["devices"])
MAX_ACTIVE_DEVICES = max(1, int(os.environ.get("HCA_MAX_ACTIVE_DEVICES", "100")))


class DeviceRegistration(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    public_key_pem: str = Field(min_length=100, max_length=2000)
    surface: Surface = "browser_overlay"
    # HCA-P19: the tenant the device answers for; omitted means the default
    # tenant so pre-P19 registrations keep working.
    tenant_id: str = Field(default=models.DEFAULT_TENANT_ID, min_length=1, max_length=100)


class GoogleLink(BaseModel):
    access_token: str = Field(min_length=1, max_length=8192)


class DevicePatch(BaseModel):
    priority: int


class DeviceView(UTCJSONModel):
    device_id: str
    tenant_id: str
    label: str
    surface: str
    priority: int = 100
    created_at: datetime
    revoked_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    google_email: Optional[str] = None
    google_linked_at: Optional[datetime] = None
    state: str = "active"
    superseded_by: Optional[str] = None
    previous_valid_until: Optional[datetime] = None


def _view(d: models.Device) -> DeviceView:
    return DeviceView(device_id=d.id, tenant_id=d.tenant_id, label=d.label, surface=d.surface, priority=d.priority if d.priority is not None else 100, created_at=d.created_at,
                      revoked_at=d.revoked_at, last_seen_at=d.last_seen_at,
                      google_email=d.google_email, google_linked_at=d.google_linked_at,
                      state=("revoked" if d.revoked_at is not None and d.superseded_by is None else "expired" if d.revoked_at is not None else "rotating" if d.superseded_by else "active"),
                      superseded_by=d.superseded_by, previous_valid_until=d.previous_valid_until)


class DeviceRotate(BaseModel):
    public_key_pem: str = Field(min_length=100, max_length=2000)


def device_rotation_overlap_hours() -> int:
    try:
        return max(0, int(os.environ.get("HCA_DEVICE_ROTATION_OVERLAP_HOURS", "24")))
    except ValueError:
        return 24


@router.post("/self/rotate", response_model=DeviceView, responses={409: {"description": "Device already rotated or revoked"}})
def rotate_self_device(body: DeviceRotate, db: Session = Depends(get_db), device: models.Device = Depends(require_device)):
    """HCA-P23 Day 3 (selection 1, docs/decisions/DECISIONS_20261015.md): the
    calling device registers its successor key itself. The successor carries
    tenant, label, surface, priority and Google link; the old row is marked
    superseded_by with previous_valid_until = now + HCA_DEVICE_ROTATION_OVERLAP_HOURS
    (default 24) and keeps verifying until then. Idempotent: the same PEM again
    returns the same successor (the client may retry after a failed local
    write). 409 once rotated with a different key, or when revoked. Declared
    before the admin /{device_id} routes so the literal path wins."""
    try:
        new_id = device_id_for_public_key_pem(body.public_key_pem)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="public_key_pem must be an ECDSA P-256 public key in SPKI PEM form") from exc
    if device.superseded_by:
        successor = db.get(models.Device, device.superseded_by)
        if successor and successor.public_key_pem.strip() == body.public_key_pem.strip():
            return _view(successor)
        raise HTTPException(status_code=409, detail="device already rotated")
    if device.revoked_at is not None:
        raise HTTPException(status_code=409, detail="device is revoked")
    if new_id == device.id:
        raise HTTPException(status_code=409, detail="new key must differ from current key")
    existing = db.get(models.Device, new_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="successor key is already registered")
    now = datetime.now(timezone.utc)
    until = now + timedelta(hours=device_rotation_overlap_hours())
    successor = models.Device(id=new_id, tenant_id=device.tenant_id, label=device.label, surface=device.surface,
        priority=device.priority, public_key_pem=body.public_key_pem.strip() + "\n", created_at=now,
        google_sub=device.google_sub, google_email=device.google_email, google_linked_at=device.google_linked_at)
    db.add(successor)
    device.superseded_by = new_id
    device.previous_valid_until = until
    db.commit()
    # Durable trace of the rotation on the tenant's event log (both devices
    # receive it; the stream handler ignores unknown event names). The old
    # device keeps verifying until previous_valid_until, then require_device
    # revokes it on its next signed call (mirrors api key rotation, P21b).
    try:
        events.publish("device.rotated", {"device_id": device.id, "successor_id": new_id,
                                          "previous_valid_until": until.isoformat()},
                       tenant_id=device.tenant_id, device_ids=[device.id, new_id])
    except Exception:  # noqa: BLE001  the rotation itself is already committed
        pass
    return _view(successor)


@router.post("", response_model=DeviceView, status_code=201)
def register_device(body: DeviceRegistration, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    try:
        device_id = device_id_for_public_key_pem(body.public_key_pem)
    except Exception as e:
        raise HTTPException(status_code=422, detail="public_key_pem must be an ECDSA P-256 public key in SPKI PEM form") from e
    tenant = db.get(models.Tenant, body.tenant_id)
    if tenant is None or tenant.disabled_at is not None:
        raise HTTPException(status_code=422, detail="unknown or disabled tenant")
    from .requests_api import _lock_tenant_for_quota
    _lock_tenant_for_quota(db, tenant.id)
    existing = db.get(models.Device, device_id)
    if existing is not None:
        if existing.revoked_at is not None:
            raise HTTPException(status_code=409, detail="device key was revoked; generate a new key")
        if existing.tenant_id != body.tenant_id:
            # A key is one device in one tenant; moving it would carry its
            # audit history across the boundary.
            raise HTTPException(status_code=409, detail="device key is registered under another tenant")
        existing.label = body.label
        db.commit()
        return _view(existing)
    active = db.execute(select(func.count(models.Device.id)).where(models.Device.tenant_id == tenant.id,
        models.Device.revoked_at.is_(None))).scalar_one()
    if active >= MAX_ACTIVE_DEVICES:
        raise HTTPException(status_code=429, detail="tenant active device limit reached", headers={"Retry-After": "60"})
    device = models.Device(id=device_id, tenant_id=body.tenant_id, label=body.label, surface=body.surface,
                           public_key_pem=body.public_key_pem.strip() + "\n",
                           created_at=datetime.now(timezone.utc))
    db.add(device)
    db.commit()
    return _view(device)


@router.get("", response_model=list[DeviceView], responses={200: {"headers": {"X-HCA-Next-Cursor": {"description": "Pass as after for the next page", "schema": {"type": "string"}}}}})
def list_devices(response: Response, limit: int = Query(default=200, ge=1, le=500),
                 after: str = Query(default="", max_length=100),
                 db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    rows = db.execute(select(models.Device).where(models.Device.id > after)
                      .order_by(models.Device.id).limit(limit + 1)).scalars().all()
    if len(rows) > limit:
        rows = rows[:limit]
        response.headers["X-HCA-Next-Cursor"] = rows[-1].id
    return [_view(d) for d in rows]


@router.post("/{device_id}/revoke", response_model=DeviceView)
def revoke_device(device_id: str, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    device = db.get(models.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    if device.revoked_at is None:
        device.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return _view(device)


def _set_priority(device: models.Device, priority: int, db: Session) -> DeviceView:
    if priority < 0 or priority > 1000:
        raise HTTPException(status_code=422, detail="priority must be between 0 and 1000")
    device.priority = priority
    db.commit()
    return _view(device)


@router.patch("/self", response_model=DeviceView)
def patch_self(body: DevicePatch, db: Session = Depends(get_db), device: models.Device = Depends(require_linked_device)):
    """The calling device sets its own place in the surface ladder (HCA-P14
    priority controls). Same gate as answering: a registered, signed and,
    when the gate is on, Google-linked device. Only the owner's own devices
    can move themselves; nobody can move another device this way. Declared
    before the admin PATCH /{device_id} so the literal path self wins."""
    device = require_current_device(db, device)
    return _set_priority(device, body.priority, db)


@router.patch("/{device_id}", response_model=DeviceView)
def patch_device(device_id: str, body: DevicePatch, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    """Set the device's place in the surface ladder (HCA-P14). Lower is
    earlier; equal values share a rank. The owner sets this once from the
    runbook Terminal block."""
    device = db.get(models.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    return _set_priority(device, body.priority, db)


@router.get("/self", response_model=DeviceView)
def self_view(device: models.Device = Depends(require_device)):
    """The calling device's own row; the options page shows the linked account from it."""
    return _view(device)


@router.post("/self/google-link", response_model=DeviceView)
def google_link(body: GoogleLink, db: Session = Depends(get_db), device: models.Device = Depends(require_device)):
    try:
        identity = googleauth.verify_access_token(body.access_token, tenancy.owner_emails_for(db, device.tenant_id))
    except googleauth.GoogleAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    row = db.get(models.Device, device.id)
    row.google_sub = identity.sub
    row.google_email = identity.email
    row.google_linked_at = datetime.now(timezone.utc)
    db.commit()
    return _view(row)
