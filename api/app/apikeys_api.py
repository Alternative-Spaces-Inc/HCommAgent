from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .db import get_db
from .apikeys import issue_api_key
from .auth import require_admin_bearer

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])


class ApiKeyIssueRequest(BaseModel):
    caller: str


class ApiKeyIssueResponse(BaseModel):
    api_key: str
    prefix: str
    caller: str
    created_at: datetime


class ApiKeyView(BaseModel):
    """One key row without any secret material: the prefix is the lookup
    handle, the hash is never returned."""
    prefix: str
    caller: str
    created_at: datetime
    revoked_at: Optional[datetime] = None


def _view(row: models.ApiKey) -> ApiKeyView:
    return ApiKeyView(prefix=row.key_prefix, caller=row.caller, created_at=row.created_at, revoked_at=row.revoked_at)


@router.post("", response_model=ApiKeyIssueResponse, status_code=201)
def issue_key(body: ApiKeyIssueRequest, db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    result = issue_api_key(db, body.caller)
    return result


@router.get("", response_model=list[ApiKeyView])
def list_keys(db: Session = Depends(get_db), _admin=Depends(require_admin_bearer)):
    rows = db.execute(select(models.ApiKey).order_by(models.ApiKey.created_at)).scalars().all()
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
