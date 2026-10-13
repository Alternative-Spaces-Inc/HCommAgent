import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .db import get_db
from .apikeys import issue_api_key, normalize_scopes, record_key_event, SCOPES
from .auth import require_admin_bearer
from .json_dates import UTCJSONModel

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])


class ApiKeyIssueRequest(BaseModel):
    caller: str = Field(min_length=1, max_length=100)
    # HCA-P19: the tenant the key acts for; omitted means the default tenant
    # so pre-P19 callers keep working.
    tenant_id: str = Field(default=models.DEFAULT_TENANT_ID, min_length=1, max_length=100)
    # HCA-P21b: subset of apikeys.SCOPES; omitted means every scope.
    scopes: Optional[list[str]] = Field(default=None, min_length=1, max_length=len(SCOPES),
                                        description="Subset of asks:create, asks:read, asks:cancel, webhooks:manage; omitted means all four")


class ApiKeyScopesPatch(BaseModel):
    scopes: list[str] = Field(min_length=1, max_length=len(SCOPES),
                              description="Replacement scope list, a non-empty subset of the four known scopes")


class ApiKeyIssueResponse(UTCJSONModel):
    api_key: str
    prefix: str
    caller: str
    tenant_id: str
    created_at: datetime
    scopes: list[str]


class ApiKeyEventView(UTCJSONModel):
    """One lifecycle event of a key: issued, scopes_changed or revoked."""
    id: str
    action: str
    actor: str
    detail: Optional[dict[str, Any]] = None
    at: datetime


class ApiKeyView(UTCJSONModel):
    """One key row without any secret material: the prefix is the lookup
    handle, the hash is never returned."""
    prefix: str
    caller: str
    tenant_id: str
    created_at: datetime
    revoked_at: Optional[datetime] = None
    scopes: list[str]


def _view(row: models.ApiKey) -> ApiKeyView:
    return ApiKeyView(prefix=row.key_prefix, caller=row.caller, tenant_id=row.tenant_id, created_at=row.created_at,
                      revoked_at=row.revoked_at, scopes=row.scope_list)


def _event_view(e: models.ApiKeyEvent) -> ApiKeyEventView:
    detail = None
    if e.detail:
        try:
            detail = json.loads(e.detail)
        except ValueError:
            detail = {"raw": e.detail}
    return ApiKeyEventView(id=e.id, action=e.action, actor=e.actor, detail=detail, at=e.at)


def _row_by_prefix(db: Session, prefix: str) -> models.ApiKey:
    row = db.execute(select(models.ApiKey).where(models.ApiKey.key_prefix == prefix)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="api key not found")
    return row


@router.post("", response_model=ApiKeyIssueResponse, status_code=201)
def issue_key(body: ApiKeyIssueRequest, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    try:
        return issue_api_key(db, body.caller, body.tenant_id, body.scopes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.get("", response_model=list[ApiKeyView], responses={200: {"headers": {"X-HCA-Next-Cursor": {"description": "Pass as after for the next page", "schema": {"type": "string"}}}}})
def list_keys(response: Response, limit: int = Query(default=200, ge=1, le=500),
               after: str = Query(default="", max_length=100),
               db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    rows = db.execute(select(models.ApiKey).where(models.ApiKey.key_prefix > after)
                      .order_by(models.ApiKey.key_prefix).limit(limit + 1)).scalars().all()
    if len(rows) > limit:
        rows = rows[:limit]
        response.headers["X-HCA-Next-Cursor"] = rows[-1].key_prefix
    return [_view(r) for r in rows]


@router.post("/{prefix}/revoke", response_model=ApiKeyView)
def revoke_key(prefix: str, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    """Revoke by the visible prefix (HCA-P10). Idempotent like device
    revoke: a second call returns the same row with its original
    revoked_at. From then on verify_api_key refuses the key, so every
    agent route answers 403 for it."""
    row = _row_by_prefix(db, prefix)
    if row.revoked_at is None:
        now = datetime.now(timezone.utc)
        row.revoked_at = now
        record_key_event(db, row, "revoked", "admin", {"prefix": prefix}, at=now)
        db.commit()
    return _view(row)


@router.patch("/{prefix}", response_model=ApiKeyView)
def patch_key_scopes(prefix: str, body: ApiKeyScopesPatch, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    """HCA-P21b: replace the scope list of a live key. The change and its
    scopes_changed event commit together; a revoked key is refused with 409
    because its scopes no longer matter and silently editing it would hide
    the revocation."""
    row = _row_by_prefix(db, prefix)
    if row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="api key is revoked")
    try:
        after = normalize_scopes(body.scopes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    before = row.scope_list
    if after != before:
        now = datetime.now(timezone.utc)
        row.scopes = json.dumps(after)
        record_key_event(db, row, "scopes_changed", "admin", {"before": before, "after": after}, at=now)
        db.commit()
    return _view(row)


@router.get("/{prefix}/events", response_model=list[ApiKeyEventView])
def list_key_events(prefix: str, limit: int = Query(default=100, ge=1, le=500),
                    db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    """HCA-P21b: lifecycle events of one key, newest first."""
    row = _row_by_prefix(db, prefix)
    rows = db.execute(select(models.ApiKeyEvent).where(models.ApiKeyEvent.api_key_id == row.id)
                      .order_by(models.ApiKeyEvent.at.desc(), models.ApiKeyEvent.id.desc()).limit(limit)).scalars().all()
    return [_event_view(e) for e in rows]
