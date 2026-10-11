"""HCA-P21a routes: the caller's webhook destination registry.

POST   /v1/webhooks               register a URL, secret returned once
GET    /v1/webhooks               the caller's destinations, no secrets
POST   /v1/webhooks/{id}/rotate   new secret returned once, old valid 24 h
DELETE /v1/webhooks/{id}          revoke (asks naming it stop delivering)

All under the caller's api key; a destination belongs to one caller in one
tenant and another caller's id is a 404, never a 403, so ids do not leak.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import models, webhook_destinations as registry
from .auth import require_agent_key
from .db import get_db

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


class WebhookRegisterRequest(BaseModel):
    url: str


class WebhookDestinationView(BaseModel):
    """A destination without secret material; secret_prefix identifies the
    secret in use so a receiver can tell which one it holds."""
    id: str
    url: str
    caller: str
    tenant_id: str
    secret_prefix: str
    pinned_addresses: Optional[List[str]] = None
    created_at: datetime
    rotated_at: Optional[datetime] = None
    previous_valid_until: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class WebhookSecretResponse(WebhookDestinationView):
    secret: str


def _view(d: models.WebhookDestination) -> dict:
    return {
        "id": d.id, "url": d.url, "caller": d.caller, "tenant_id": d.tenant_id,
        "secret_prefix": d.secret_prefix, "pinned_addresses": d.pinned_addresses,
        "created_at": d.created_at, "rotated_at": d.rotated_at,
        "previous_valid_until": d.previous_valid_until, "revoked_at": d.revoked_at,
    }


def _raise(exc: registry.RegistryError):
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None


@router.post("", response_model=WebhookSecretResponse, status_code=201)
def register_destination(body: WebhookRegisterRequest, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    try:
        dest, secret = registry.register(db, tenant_id=key.tenant_id, caller=key.caller, url=body.url)
    except registry.RegistryError as exc:
        _raise(exc)
    return {**_view(dest), "secret": secret}


@router.get("", response_model=List[WebhookDestinationView])
def list_destinations(include_revoked: bool = False, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    rows = registry.all_for(db, key.tenant_id, key.caller) if include_revoked else registry.active_for(db, key.tenant_id, key.caller)
    return [_view(d) for d in rows]


@router.post("/{destination_id}/rotate", response_model=WebhookSecretResponse)
def rotate_destination(destination_id: str, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    try:
        dest, secret = registry.rotate(db, tenant_id=key.tenant_id, caller=key.caller, destination_id=destination_id)
    except registry.RegistryError as exc:
        _raise(exc)
    return {**_view(dest), "secret": secret}


@router.delete("/{destination_id}", response_model=WebhookDestinationView)
def revoke_destination(destination_id: str, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    try:
        dest = registry.revoke(db, tenant_id=key.tenant_id, caller=key.caller, destination_id=destination_id)
    except registry.RegistryError as exc:
        _raise(exc)
    return _view(dest)
