"""HCA-P19 Day 2: idempotency keys on create, answer and cancel.

A client that retries a POST after a timeout must not create a second
ask, a second decision or a second cancel. It sends the same
Idempotency-Key header (any string up to 200 characters, unique per
caller or device) and gets the stored response back, same status, same
body. The same key with a different body or resource is a client error (422), never
a silent replay of the first body.

Reservation, business state, event/outbox and stored response commit together.
A concurrent retry waits on the database uniqueness constraint.

Keys are scoped to (tenant, scope, route): scope is the api key's caller
for agent routes and the device id for the answer route, so two callers
can use the same key text without touching each other. Rows are kept
IDEMPOTENCY_TTL_SECONDS (default 24 h) and swept by the expiry job.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from .json_dates import utc_datetime

HEADER = "Idempotency-Key"
MAX_KEY = 200
TTL_SECONDS = int(os.environ.get("HCA_IDEMPOTENCY_TTL_SECONDS", str(24 * 3600)))


def key_from(request: Request) -> Optional[str]:
    raw = request.headers.get(HEADER)
    if raw is None:
        return None
    key = raw.strip()
    if not key or len(key) > MAX_KEY:
        raise HTTPException(status_code=422, detail=f"{HEADER} must be 1 to {MAX_KEY} characters")
    return key


def body_hash(payload: Any, resource: str = "") -> str:
    # Include the canonical operation and resource, so a key cannot answer
    # or cancel a different ask whose JSON body happens to be identical.
    canonical = json.dumps({"method": "POST", "resource": resource, "body": jsonable_encoder(payload)},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _find(db, tenant_id, scope, route, key):
    return db.execute(select(models.IdempotencyKey).where(
        models.IdempotencyKey.tenant_id == tenant_id,
        models.IdempotencyKey.scope == scope,
        models.IdempotencyKey.route == route,
        models.IdempotencyKey.key == key,
    )).scalar_one_or_none()


def replay(db: Session, tenant_id: str, scope: str, route: str, key: Optional[str], payload: Any,
           resource: str = "") -> Optional[JSONResponse]:
    """The committed response for this operation, or None when the key is new."""
    if key is None:
        return None
    row = _find(db, tenant_id, scope, route, key)
    if row is None:
        return None
    if row.request_hash != body_hash(payload, resource):
        raise HTTPException(status_code=422, detail=f"{HEADER} was already used with a different request")
    if row.status_code == 0:
        # Reservation rows must never commit before store(). Fail safely if
        # a legacy/manual writer left an incomplete record behind.
        raise HTTPException(status_code=409, detail="idempotent request is incomplete")
    return JSONResponse(status_code=row.status_code, content=row.response, headers={"Idempotent-Replayed": "true"})


def reserve(db: Session, tenant_id: str, scope: str, route: str, key: Optional[str], payload: Any,
            resource: str = "") -> Optional[JSONResponse]:
    """Reserve the unique key before effects, in the business transaction.

    A simultaneous retry waits on the unique insert. If the first writer
    commits, the loser rolls back and replays it; if it rolls back, the
    waiting insert succeeds and performs the operation. No partial result
    or reservation is committed independently of the business change.
    """
    existing = replay(db, tenant_id, scope, route, key, payload, resource)
    if existing is not None or key is None:
        return existing
    db.add(models.IdempotencyKey(tenant_id=tenant_id, scope=scope, route=route, key=key,
                                request_hash=body_hash(payload, resource), status_code=0,
                                response={}, created_at=datetime.now(timezone.utc)))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = replay(db, tenant_id, scope, route, key, payload, resource)
        if existing is None:
            raise
        return existing
    return None


def _datetime_as_pydantic_json(value: datetime) -> str:
    """Match UTCJSONModel on the first response and every stored replay."""
    text = utc_datetime(value).isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def store(db: Session, tenant_id: str, scope: str, route: str, key: Optional[str], payload: Any,
          status_code: int, response: Any, resource: str = "") -> None:
    """Complete the reservation in the caller's transaction; never commit."""
    if key is None:
        return
    if isinstance(response, BaseModel):
        content = response.model_dump(mode="json")
    else:
        content = jsonable_encoder(response, custom_encoder={datetime: _datetime_as_pydantic_json})
    row = _find(db, tenant_id, scope, route, key)
    if row is None or row.request_hash != body_hash(payload, resource):
        raise RuntimeError("idempotency key was not reserved for this operation")
    row.status_code = status_code
    row.response = content
    db.flush()


def discard(db, tenant_id, scope, route, key):
    """Remove an uncompleted reservation when applying expiry instead of an answer."""
    if key is None:
        return
    row = _find(db, tenant_id, scope, route, key)
    if row is not None and row.status_code == 0:
        db.delete(row)
        db.flush()


def sweep(db: Session, now: Optional[datetime] = None, batch: int = 500) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=TTL_SECONDS)
    ids = list(db.execute(select(models.IdempotencyKey.id).where(models.IdempotencyKey.created_at < cutoff)
                         .order_by(models.IdempotencyKey.id).limit(max(1, min(batch, 500)))).scalars())
    result = db.execute(delete(models.IdempotencyKey).where(models.IdempotencyKey.id.in_(ids)))
    db.commit()
    return result.rowcount or 0
