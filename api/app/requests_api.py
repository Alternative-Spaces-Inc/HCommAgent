import os
from datetime import datetime, timedelta, timezone

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import models
from .db import get_db
from .schemas import CollectionRequest, RequestReceipt, AnswerSubmission, AnswerRecord, RequestView, CancelRequestBody
from .events import publish
from .webhooks import deliver_outcome
from .auth import require_agent_key, require_linked_device, google_link_required
from .db import SessionLocal
from . import routing
from . import controls
from . import idempotency

router = APIRouter(prefix="/v1/requests", tags=["requests"])

TERMINAL_STATES = {"answered", "expired", "withdrawn", "cancelled"}

# HCA-P19: every ask gets an expires_at. Without a deadline the ask lives
# HCA_DEFAULT_LIFETIME_SECONDS (default 7 days); a deadline may not sit in
# the past or further out than HCA_MAX_LIFETIME_SECONDS (default 30 days).
DEFAULT_LIFETIME_SECONDS = int(os.environ.get("HCA_DEFAULT_LIFETIME_SECONDS", str(7 * 86400)))
MAX_LIFETIME_SECONDS = int(os.environ.get("HCA_MAX_LIFETIME_SECONDS", str(30 * 86400)))


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _expires_at(now: datetime, deadline_at: Optional[datetime]) -> datetime:
    deadline = _utc(deadline_at)
    if deadline is None:
        return now + timedelta(seconds=DEFAULT_LIFETIME_SECONDS)
    if deadline <= now:
        raise HTTPException(status_code=422, detail="deadline_at is not in the future")
    if (deadline - now).total_seconds() > MAX_LIFETIME_SECONDS:
        raise HTTPException(status_code=422, detail=f"deadline_at is more than {MAX_LIFETIME_SECONDS} seconds away")
    return deadline


def _locked_ask(db: Session, request_id: str) -> Optional[models.Ask]:
    """Load the ask row with SELECT ... FOR UPDATE where the database supports
    it (Postgres in production and CI), so two terminal transitions
    (answer versus cancel, answer versus expiry, two answers) serialise and
    exactly one wins; the loser re-reads a terminal state and gets 409.
    SQLite ignores the lock hint and relies on its single writer."""
    return db.execute(select(models.Ask).where(models.Ask.id == request_id).with_for_update()).scalar_one_or_none()


def _conflict(ask: models.Ask) -> HTTPException:
    """Deterministic 409 body: the same shape for every losing transition."""
    return HTTPException(status_code=409, detail={"error": "request_terminal", "request_id": ask.id, "state": ask.state})


def _own_ask(db: Session, request_id: str, key: models.ApiKey, lock: bool = False) -> models.Ask:
    """The caller's own ask within its tenant; 404 otherwise, so neither ids
    nor existence leak across callers or tenants."""
    ask = _locked_ask(db, request_id) if lock else db.get(models.Ask, request_id)
    if ask is None or ask.tenant_id != key.tenant_id or ask.caller != key.caller:
        raise HTTPException(status_code=404, detail="request not found")
    return ask


def _ask_to_collection_request(ask: models.Ask) -> dict:
    return {
        "caller": ask.caller,
        "audience": ask.audience,
        "title": ask.title,
        "why": ask.why,
        "path_hint": ask.path_hint,
        "zoom": ask.zoom,
        "control": ask.control,
        "options_or_fields": [
            {"id": o.option_id, "label": o.label, "description": o.description}
            for o in sorted(ask.options, key=lambda o: o.position)
        ],
        "suggested": ask.suggested,
        "red_gate": ask.red_gate,
        "voice": ask.voice,
        "deadline_at": ask.deadline_at,
        "expiry_policy": ask.expiry_policy,
        "deliveries": [
            {"surface": d.surface, "delivered_at": d.delivered_at, "state": d.state}
            for d in ask.deliveries
        ],
        "deliver_to": ask.deliver_to,
    }


def _audit_to_answer_record(row: models.AuditRow) -> dict:
    return {
        "request_id": row.ask_id,
        "control": row.control,
        "value": row.value,
        "answered_by": row.answered_by,
        "answered_role": row.answered_role,
        "answered_at": row.answered_at,
        "surface": row.surface,
        "device": row.device,
        "path_taken": row.path_taken,
        "duration_ms": row.duration_ms,
        "voice_used": row.voice_used,
        "passthrough": row.passthrough,
    }


@router.post("", status_code=201, response_model=RequestReceipt)
def create_request(body: CollectionRequest, request: Request, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    if body.caller != key.caller:
        raise HTTPException(status_code=403, detail="caller does not match the api key")
    payload = body.model_dump(mode="json")
    idem_key = idempotency.key_from(request)
    replayed = idempotency.replay(db, key.tenant_id, key.caller, "create", idem_key, payload)
    if replayed is not None:
        return replayed
    try:
        controls.validate_request_shape(body.control, body.options_or_fields, body.title, body.why, body.caller)
    except controls.ControlError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    now = datetime.now(timezone.utc)
    expires_at = _expires_at(now, body.deadline_at)
    ask = models.Ask(
        tenant_id=key.tenant_id,
        caller=body.caller,
        audience=body.audience,
        title=body.title,
        why=body.why,
        path_hint=body.path_hint.model_dump() if body.path_hint else None,
        zoom=body.zoom,
        control=body.control,
        suggested=body.suggested.model_dump(),
        red_gate=body.red_gate.model_dump() if body.red_gate else None,
        voice=body.voice.model_dump() if body.voice else None,
        deadline_at=_utc(body.deadline_at),
        expiry_policy=body.expiry_policy,
        deliver_to=body.deliver_to.model_dump(),
        state="queued",
        created_at=now,
        expires_at=expires_at,
    )
    db.add(ask)
    db.flush()

    for i, opt in enumerate(body.options_or_fields):
        db.add(models.AskOption(ask_id=ask.id, option_id=opt.id, label=opt.label, description=opt.description, position=i))

    for d in body.deliveries or []:
        db.add(models.AskDelivery(ask_id=ask.id, surface=d.surface, delivered_at=d.delivered_at, state=d.state))

    db.commit()
    receipt = {"request_id": ask.id, "state": ask.state}
    # HCA-P14: the created event names the rank 0 devices; later ranks get a
    # request.escalated envelope when their window opens (app/routing.py).
    eligible = routing.eligible_devices(db, now, google_link_required(), ask.tenant_id)
    visible = routing.visible_to(db, ask, now, google_link_required())
    publish("request.created", _ask_to_collection_request(ask) | {"request_id": ask.id, "visible_to": visible},
            tenant_id=ask.tenant_id, device_ids=visible)
    delays = routing.escalation_delays(ask.created_at, routing.rank_count(eligible), bool(ask.red_gate), now)
    routing.schedule_escalations(ask.id, delays, SessionLocal, google_link_required, publish)
    idempotency.store(db, key.tenant_id, key.caller, "create", idem_key, payload, 201, receipt)
    return receipt


@router.get("/{request_id}", response_model=RequestView)
def get_request(request_id: str, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    ask = _own_ask(db, request_id, key)

    answer = None
    if ask.answer is not None:
        answer = _audit_to_answer_record(ask.answer)

    return {
        "request_id": ask.id,
        "state": ask.state,
        "created_at": ask.created_at,
        "expires_at": ask.expires_at,
        "request": _ask_to_collection_request(ask),
        "answer": answer,
    }



@router.post("/{request_id}/answer", status_code=201, response_model=AnswerRecord)
def answer_request(request_id: str, body: AnswerSubmission, request: Request, background: BackgroundTasks, db: Session = Depends(get_db), device: models.Device = Depends(require_linked_device)):
    payload = body.model_dump(mode="json")
    idem_key = idempotency.key_from(request)
    replayed = idempotency.replay(db, device.tenant_id, device.id, "answer", idem_key, payload)
    if replayed is not None:
        return replayed

    ask = _locked_ask(db, request_id)
    # Another tenant's ask is indistinguishable from a missing one.
    if ask is None or ask.tenant_id != device.tenant_id:
        raise HTTPException(status_code=404, detail="request not found")

    if ask.state in TERMINAL_STATES:
        raise _conflict(ask)

    # The submitted control must be the ask's control: a radio answer to an
    # approve_deny ask was accepted with 201 before P19.
    if body.control != ask.control:
        raise HTTPException(status_code=422, detail=f"control {body.control!r} does not match the request control {ask.control!r}")

    try:
        value = controls.validate_answer_value(ask.control, body.value, (o.option_id for o in ask.options))
    except controls.ControlError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    now = datetime.now(timezone.utc)
    row = models.AuditRow(
        ask_id=ask.id,
        tenant_id=ask.tenant_id,
        control=body.control,
        value=value,
        answered_by=body.answered_by or device.label,
        answered_role=body.answered_role,
        answered_at=now,
        surface=body.surface,
        device=device.id,
        path_taken=body.path_taken,
        duration_ms=body.duration_ms,
        voice_used=body.voice_used,
        passthrough=body.passthrough,
    )
    db.add(row)
    ask.state = "answered"
    try:
        db.commit()
    except IntegrityError:
        # Two answers for the same ask raced past the state check above
        # (seen 2026-10-03 when the extension replayed a queued answer from
        # two code paths in the same second). The unique constraint on
        # audit_rows.ask_id keeps one decision per ask; the loser gets the
        # same 409 a late second answer gets, not a 500.
        db.rollback()
        current = db.get(models.Ask, request_id)
        raise _conflict(current) from None

    record = _audit_to_answer_record(row)
    publish("request.answered", record, tenant_id=ask.tenant_id)
    background.add_task(deliver_outcome, ask.id, "request.answered", ask.state, record, ask.deliver_to)
    idempotency.store(db, ask.tenant_id, device.id, "answer", idem_key, payload, 201, record)
    return record


def _outcome_of(value) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("decision")
    return None


@router.post("/{request_id}/cancel", response_model=RequestReceipt)
def cancel_request(request_id: str, body: CancelRequestBody, request: Request, background: BackgroundTasks, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    payload = body.model_dump(mode="json")
    idem_key = idempotency.key_from(request)
    replayed = idempotency.replay(db, key.tenant_id, key.caller, "cancel", idem_key, payload)
    if replayed is not None:
        return replayed

    ask = _own_ask(db, request_id, key, lock=True)

    if ask.state in TERMINAL_STATES:
        raise _conflict(ask)

    now = datetime.now(timezone.utc)
    row = models.AuditRow(
        ask_id=ask.id,
        tenant_id=ask.tenant_id,
        control=ask.control,
        value={"decision": "cancel", "reason": body.reason},
        answered_by=None,
        answered_role=None,
        answered_at=now,
        surface="web",
        device=None,
        path_taken=None,
        duration_ms=None,
        voice_used=None,
        passthrough={"reason": body.reason},
    )
    db.add(row)
    ask.state = "cancelled"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict(db.get(models.Ask, request_id)) from None

    record = _audit_to_answer_record(row)
    publish("request.cancelled", record, tenant_id=ask.tenant_id)
    background.add_task(deliver_outcome, ask.id, "request.cancelled", ask.state, record, ask.deliver_to)
    receipt = {"request_id": ask.id, "state": ask.state}
    idempotency.store(db, key.tenant_id, key.caller, "cancel", idem_key, payload, 200, receipt)
    return receipt


audit_router = APIRouter(prefix="/v1/audit", tags=["audit"])


@audit_router.get("", response_model=list[AnswerRecord])
def query_audit(
    agent: Optional[str] = Query(default=None, description="Filter by agent ID"),
    from_: Optional[datetime] = Query(default=None, alias="from", description="Filter by start timestamp"),
    to: Optional[datetime] = Query(default=None, description="Filter by end timestamp"),
    surface: Optional[str] = Query(default=None, description="Filter by delivery or answer surface"),
    outcome: Optional[str] = Query(default=None, description="Filter by decision or answer outcome"),
    db: Session = Depends(get_db),
    device: models.Device = Depends(require_linked_device),
):
    # HCA-P19: the audit read is scoped to the authenticated device's tenant.
    stmt = (select(models.AuditRow, models.Ask)
            .join(models.Ask, models.AuditRow.ask_id == models.Ask.id)
            .where(models.AuditRow.tenant_id == device.tenant_id))

    if agent:
        stmt = stmt.where(models.Ask.caller == agent)
    if surface:
        stmt = stmt.where(models.AuditRow.surface == surface)
    if from_:
        stmt = stmt.where(models.AuditRow.answered_at >= from_)
    if to:
        stmt = stmt.where(models.AuditRow.answered_at <= to)

    rows = db.execute(stmt).all()

    records = []
    for row, _ask in rows:
        if outcome and _outcome_of(row.value) != outcome:
            continue
        records.append(_audit_to_answer_record(row))
    return records
