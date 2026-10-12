"""Bounded admin export and explicit deletion of completed decision records."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, exists, select

from . import models
from .auth import require_admin_bearer
from .schemas import RequestView
from .db import get_db
from .requests_api import _ask_to_collection_request, _audit_to_answer_record, TERMINAL_STATES

router = APIRouter(prefix="/v1/tenants", tags=["data"])


def tenant_for(db, tenant_id):
    tenant = db.get(models.Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return tenant


@router.get("/{tenant_id}/records", response_model=list[RequestView], responses={200: {"headers": {"X-HCA-Next-Cursor": {"description": "Pass as after for the next page", "schema": {"type": "string"}}}}})
def export_records(tenant_id: str, response: Response, limit: int = Query(default=200, ge=1, le=500),
                   after: str = Query(default="", max_length=100), db=Depends(get_db), admin=Depends(require_admin_bearer)):
    tenant_for(db, tenant_id)
    rows = db.execute(select(models.Ask).where(models.Ask.tenant_id == tenant_id, models.Ask.id > after)
                      .order_by(models.Ask.id).limit(limit + 1)).scalars().all()
    if len(rows) > limit:
        rows = rows[:limit]
        response.headers["X-HCA-Next-Cursor"] = rows[-1].id
    return [{"request_id": r.id, "state": r.state, "created_at": r.created_at, "expires_at": r.expires_at, "request": _ask_to_collection_request(r),
             "answer": _audit_to_answer_record(r.answer) if r.answer else None} for r in rows]


class PurgeRecords(BaseModel):
    confirm_slug: str = Field(min_length=2, max_length=63)
    before: datetime
    limit: int = Field(default=200, ge=1, le=500)


class PurgeResult(BaseModel):
    purged: int = Field(ge=0)


@router.post("/{tenant_id}/records/purge", response_model=PurgeResult)
def purge_records(tenant_id: str, body: PurgeRecords, db=Depends(get_db), admin=Depends(require_admin_bearer)):
    tenant = tenant_for(db, tenant_id)
    if body.confirm_slug != tenant.slug:
        raise HTTPException(status_code=422, detail="confirm_slug must match the tenant to delete records")
    before = body.before
    if before.tzinfo is None:
        raise HTTPException(status_code=422, detail="before must include a timezone")
    before = before.astimezone(timezone.utc).replace(tzinfo=None)
    if before > datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(status_code=422, detail="before cannot be in the future")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pending_delivery = exists(select(models.WebhookDelivery.id).where(
        models.WebhookDelivery.ask_id == models.Ask.id, models.WebhookDelivery.state == "queued"))
    leased_job = exists(select(models.ScheduledJob.id).where(
        models.ScheduledJob.ask_id == models.Ask.id, models.ScheduledJob.done_at.is_(None),
        models.ScheduledJob.lease_expires_at > now, models.ScheduledJob.lease_owner.is_not(None)))
    candidates = list(db.execute(select(models.Ask.id).where(models.Ask.tenant_id == tenant_id,
        models.Ask.state.in_(TERMINAL_STATES), models.Ask.created_at < before, ~pending_delivery, ~leased_job)
        .order_by(models.Ask.id).limit(body.limit)).scalars())
    # Use the worker lock order, job -> ask/delivery, to avoid deadlocks.
    if candidates:
        db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id.in_(candidates))
                   .order_by(models.ScheduledJob.id).with_for_update()).scalars().all()
    rows = db.execute(select(models.Ask).where(models.Ask.id.in_(candidates),
        models.Ask.tenant_id == tenant_id, models.Ask.state.in_(TERMINAL_STATES), ~pending_delivery, ~leased_job)
        .order_by(models.Ask.id).with_for_update()).scalars().all()
    ids = [r.id for r in rows]
    if not ids:
        return {"purged": 0}
    for model in (models.WebhookAttempt, models.WebhookDelivery, models.ScheduledJob,
                  models.AuditRow, models.AskOption, models.AskDelivery):
        db.execute(delete(model).where(model.ask_id.in_(ids)))
    db.execute(delete(models.IdempotencyKey).where(models.IdempotencyKey.tenant_id == tenant_id,
               models.IdempotencyKey.response["request_id"].as_string().in_(ids)))
    # Historical stream payloads contain the same personal decision data.
    db.execute(delete(models.Event).where(models.Event.tenant_id == tenant_id,
               models.Event.data["request_id"].as_string().in_(ids)))
    db.execute(delete(models.WebhookDestinationEvent).where(models.WebhookDestinationEvent.tenant_id == tenant_id,
               models.WebhookDestinationEvent.detail["request_id"].as_string().in_(ids)))
    db.execute(delete(models.Ask).where(models.Ask.id.in_(ids)))
    db.commit()
    return {"purged": len(ids)}
