from datetime import datetime, timezone

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
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

router = APIRouter(prefix="/v1/requests", tags=["requests"])

TERMINAL_STATES = {"answered", "expired", "withdrawn", "cancelled"}


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
def create_request(body: CollectionRequest, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    if body.caller != key.caller:
        raise HTTPException(status_code=403, detail="caller does not match the api key")
    now = datetime.now(timezone.utc)
    ask = models.Ask(
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
        deadline_at=body.deadline_at,
        expiry_policy=body.expiry_policy,
        deliver_to=body.deliver_to.model_dump(),
        state="queued",
        created_at=now,
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
    eligible = routing.eligible_devices(db, now, google_link_required())
    visible = routing.visible_to(db, ask, now, google_link_required())
    publish("request.created", _ask_to_collection_request(ask) | {"request_id": ask.id, "visible_to": visible})
    delays = routing.escalation_delays(ask.created_at, routing.rank_count(eligible), bool(ask.red_gate), now)
    routing.schedule_escalations(ask.id, delays, SessionLocal, google_link_required, publish)
    return receipt


@router.get("/{request_id}", response_model=RequestView)
def get_request(request_id: str, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    ask = db.get(models.Ask, request_id)
    if ask is None or ask.caller != key.caller:
        raise HTTPException(status_code=404, detail="request not found")

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


def _value_fits(control: str, value, option_ids: set[str]) -> bool:
    if control in ("radio", "dropdown", "approve_deny"):
        return isinstance(value, str) and (not option_ids or value in option_ids)
    if control == "checkbox":
        return isinstance(value, list) and all((not option_ids or v in option_ids) for v in value)
    if control in ("chat", "confirm_rows"):
        return value is not None
    return True


@router.post("/{request_id}/answer", status_code=201, response_model=AnswerRecord)
def answer_request(request_id: str, body: AnswerSubmission, background: BackgroundTasks, db: Session = Depends(get_db), device: models.Device = Depends(require_linked_device)):
    ask = db.get(models.Ask, request_id)
    if ask is None:
        raise HTTPException(status_code=404, detail="request not found")

    if ask.state in TERMINAL_STATES:
        raise HTTPException(status_code=409, detail=f"request already {ask.state}")

    option_ids = {o.option_id for o in ask.options}
    if not _value_fits(body.control, body.value, option_ids):
        raise HTTPException(status_code=422, detail="value does not fit the control or the option ids")

    now = datetime.now(timezone.utc)
    row = models.AuditRow(
        ask_id=ask.id,
        control=body.control,
        value=body.value,
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
        raise HTTPException(status_code=409, detail="request already answered") from None

    record = _audit_to_answer_record(row)
    publish("request.answered", record)
    background.add_task(deliver_outcome, ask.id, "request.answered", ask.state, record, ask.deliver_to)
    return record


def _outcome_of(value) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("decision")
    return None


@router.post("/{request_id}/cancel", response_model=RequestReceipt)
def cancel_request(request_id: str, body: CancelRequestBody, background: BackgroundTasks, db: Session = Depends(get_db), key: models.ApiKey = Depends(require_agent_key)):
    ask = db.get(models.Ask, request_id)
    if ask is None or ask.caller != key.caller:
        raise HTTPException(status_code=404, detail="request not found")

    if ask.state in TERMINAL_STATES:
        raise HTTPException(status_code=409, detail=f"request already {ask.state}")

    now = datetime.now(timezone.utc)
    row = models.AuditRow(
        ask_id=ask.id,
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
    db.commit()

    record = _audit_to_answer_record(row)
    publish("request.cancelled", record)
    background.add_task(deliver_outcome, ask.id, "request.cancelled", ask.state, record, ask.deliver_to)
    return {"request_id": ask.id, "state": ask.state}


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
    stmt = select(models.AuditRow, models.Ask).join(models.Ask, models.AuditRow.ask_id == models.Ask.id)

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
