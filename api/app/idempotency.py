"""HCA-P19 Day 2: idempotency keys on create, answer and cancel.

A client that retries a POST after a timeout must not create a second
ask, a second decision or a second cancel. It sends the same
Idempotency-Key header (any string up to 200 characters, unique per
caller or device) and gets the stored response back, same status, same
body. The same key with a different body is a client error (422), never
a silent replay of the first body.

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
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models

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


def body_hash(payload: Any) -> str:
    canonical = json.dumps(jsonable_encoder(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def replay(db: Session, tenant_id: str, scope: str, route: str, key: Optional[str], payload: Any) -> Optional[JSONResponse]:
    """The stored response for this key, or None when the key is new."""
    if key is None:
        return None
    row = db.execute(select(models.IdempotencyKey).where(
        models.IdempotencyKey.tenant_id == tenant_id,
        models.IdempotencyKey.scope == scope,
        models.IdempotencyKey.route == route,
        models.IdempotencyKey.key == key,
    )).scalar_one_or_none()
    if row is None:
        return None
    if row.request_hash != body_hash(payload):
        raise HTTPException(status_code=422, detail=f"{HEADER} was already used with a different request body")
    return JSONResponse(status_code=row.status_code, content=row.response, headers={"Idempotent-Replayed": "true"})


def store(db: Session, tenant_id: str, scope: str, route: str, key: Optional[str], payload: Any, status_code: int, response: Any) -> None:
    """Record the response for later replays; a concurrent duplicate insert
    loses quietly, the first stored response wins for both."""
    if key is None:
        return
    # Same encoder FastAPI uses for the live response, so a replay is byte
    # for byte the first answer (datetimes included).
    content = jsonable_encoder(response)
    db.add(models.IdempotencyKey(tenant_id=tenant_id, scope=scope, route=route, key=key,
                                 request_hash=body_hash(payload), status_code=status_code,
                                 response=content, created_at=datetime.now(timezone.utc)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def sweep(db: Session, now: Optional[datetime] = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=TTL_SECONDS)
    result = db.execute(delete(models.IdempotencyKey).where(models.IdempotencyKey.created_at < cutoff))
    db.commit()
    return result.rowcount or 0
