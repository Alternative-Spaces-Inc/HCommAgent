from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from . import models
from .db import get_db
from .schemas import CollectionRequest, RequestReceipt, AnswerSubmission, AnswerRecord, RequestView, Option
from .events import publish

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
def create_request(body: CollectionRequest, db: Session = Depends(get_db)):
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
    publish("request.created", _ask_to_collection_request(ask) | {"request_id": ask.id})
    return receipt


@router.get("/{request_id}", response_model=RequestView)
def get_request(request_id: str, db: Session = Depends(get_db)):
    ask = db.get(models.Ask, request_id)
    if ask is None:
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
def answer_request(request_id: str, body: AnswerSubmission, db: Session = Depends(get_db)):
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
        answered_by=body.answered_by,
        answered_role=body.answered_role,
        answered_at=now,
        surface=body.surface,
        device=body.device,
        path_taken=body.path_taken,
        duration_ms=body.duration_ms,
        voice_used=body.voice_used,
        passthrough=body.passthrough,
    )
    db.add(row)
    ask.state = "answered"
    db.commit()

    record = _audit_to_answer_record(row)
    publish("request.answered", record)
    return record
