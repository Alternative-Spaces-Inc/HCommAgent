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

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .auth import require_admin_bearer, require_device, require_linked_device, require_current_device
from . import googleauth
from . import tenancy
from .db import get_db
from .devicesig import device_id_for_public_key_pem

router = APIRouter(prefix="/v1/devices", tags=["devices"])


class DeviceRegistration(BaseModel):
    label: str
    public_key_pem: str
    surface: str = "browser_overlay"
    # HCA-P19: the tenant the device answers for; omitted means the default
    # tenant so pre-P19 registrations keep working.
    tenant_id: str = models.DEFAULT_TENANT_ID


class GoogleLink(BaseModel):
    access_token: str


class DevicePatch(BaseModel):
    priority: int


class DeviceView(BaseModel):
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


def _view(d: models.Device) -> DeviceView:
    return DeviceView(device_id=d.id, tenant_id=d.tenant_id, label=d.label, surface=d.surface, priority=d.priority if d.priority is not None else 100, created_at=d.created_at,
                      revoked_at=d.revoked_at, last_seen_at=d.last_seen_at,
                      google_email=d.google_email, google_linked_at=d.google_linked_at)


@router.post("", response_model=DeviceView, status_code=201)
def register_device(body: DeviceRegistration, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    try:
        device_id = device_id_for_public_key_pem(body.public_key_pem)
    except Exception as e:
        raise HTTPException(status_code=422, detail="public_key_pem must be an ECDSA P-256 public key in SPKI PEM form") from e
    tenant = db.get(models.Tenant, body.tenant_id)
    if tenant is None or tenant.disabled_at is not None:
        raise HTTPException(status_code=422, detail="unknown or disabled tenant")
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
    device = models.Device(id=device_id, tenant_id=body.tenant_id, label=body.label, surface=body.surface,
                           public_key_pem=body.public_key_pem.strip() + "\n",
                           created_at=datetime.now(timezone.utc))
    db.add(device)
    db.commit()
    return _view(device)


@router.get("", response_model=list[DeviceView])
def list_devices(db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    rows = db.execute(select(models.Device).order_by(models.Device.created_at)).scalars().all()
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
