"""Device registration for the human surface (HCA-P6, app/devicesig.py).

POST /v1/devices        admin bearer; body {label, public_key_pem, surface?}
GET  /v1/devices        admin bearer; every device, revoked included
POST /v1/devices/{id}/revoke  admin bearer

The owner runs registration once per browser profile with the PEM the
extension's options page shows. Registering the same key again is
idempotent (200 with the existing row, label updated); a revoked key
cannot be re-registered by posting it again (409), a new key is needed.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .auth import require_admin_bearer
from .db import get_db
from .devicesig import device_id_for_public_key_pem

router = APIRouter(prefix="/v1/devices", tags=["devices"])


class DeviceRegistration(BaseModel):
    label: str
    public_key_pem: str
    surface: str = "browser_overlay"


class DeviceView(BaseModel):
    device_id: str
    label: str
    surface: str
    created_at: datetime
    revoked_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None


def _view(d: models.Device) -> DeviceView:
    return DeviceView(device_id=d.id, label=d.label, surface=d.surface, created_at=d.created_at,
                      revoked_at=d.revoked_at, last_seen_at=d.last_seen_at)


@router.post("", response_model=DeviceView, status_code=201)
def register_device(body: DeviceRegistration, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    try:
        device_id = device_id_for_public_key_pem(body.public_key_pem)
    except Exception:
        raise HTTPException(status_code=422, detail="public_key_pem must be an ECDSA P-256 public key in SPKI PEM form")
    existing = db.get(models.Device, device_id)
    if existing is not None:
        if existing.revoked_at is not None:
            raise HTTPException(status_code=409, detail="device key was revoked; generate a new key")
        existing.label = body.label
        db.commit()
        return _view(existing)
    device = models.Device(id=device_id, label=body.label, surface=body.surface,
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
