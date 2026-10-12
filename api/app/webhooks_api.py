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

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from . import models, webhook_destinations as registry
from .auth import require_agent_key
from .db import get_db

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


class WebhookRegisterRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


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


@router.get("", response_model=List[WebhookDestinationView], responses={200: {"headers": {"X-HCA-Next-Cursor": {"description": "Pass as after for the next page", "schema": {"type": "string"}}}}})
def list_destinations(response: Response, include_revoked: bool = False, limit: int = Query(default=200, ge=1, le=500),
                       after: str = Query(default="", max_length=100),
                       db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    statement = select(models.WebhookDestination).where(models.WebhookDestination.tenant_id == key.tenant_id,
        models.WebhookDestination.caller == key.caller, models.WebhookDestination.id > after)
    if not include_revoked:
        statement = statement.where(models.WebhookDestination.revoked_at.is_(None))
    rows = db.execute(statement.order_by(models.WebhookDestination.id).limit(limit + 1)).scalars().all()
    if len(rows) > limit:
        rows = rows[:limit]
        response.headers["X-HCA-Next-Cursor"] = rows[-1].id
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
