from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .db import get_db
from .apikeys import issue_api_key
from .auth import require_admin_bearer

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])


class ApiKeyIssueRequest(BaseModel):
    caller: str = Field(min_length=1, max_length=100)
    # HCA-P19: the tenant the key acts for; omitted means the default tenant
    # so pre-P19 callers keep working.
    tenant_id: str = Field(default=models.DEFAULT_TENANT_ID, min_length=1, max_length=100)


class ApiKeyIssueResponse(BaseModel):
    api_key: str
    prefix: str
    caller: str
    tenant_id: str
    created_at: datetime


class ApiKeyView(BaseModel):
    """One key row without any secret material: the prefix is the lookup
    handle, the hash is never returned."""
    prefix: str
    caller: str
    tenant_id: str
    created_at: datetime
    revoked_at: Optional[datetime] = None


def _view(row: models.ApiKey) -> ApiKeyView:
    return ApiKeyView(prefix=row.key_prefix, caller=row.caller, tenant_id=row.tenant_id, created_at=row.created_at, revoked_at=row.revoked_at)


@router.post("", response_model=ApiKeyIssueResponse, status_code=201)
def issue_key(body: ApiKeyIssueRequest, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    try:
        return issue_api_key(db, body.caller, body.tenant_id)
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
    row = db.execute(select(models.ApiKey).where(models.ApiKey.key_prefix == prefix)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="api key not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return _view(row)
