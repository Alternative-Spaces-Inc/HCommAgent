"""The auto-deny expiry sweep.

docs/decisions/DECISIONS_20260929.md, decision 2 (13:52 PT): the red-gate
default for a deadline with no answer is Automatic Deny, with an explicit
Deny answer record written back to the caller. auto_deny is also the
column default on Ask.expiry_policy (app/models.py), so this is the
behavior for every ask unless a caller opts into a different
expiry_policy on creation.

Three expiry_policy values, from api/app/schemas.py:
  auto_deny (default): write an explicit Deny audit row (the answer
    record the decision describes), then move the ask to expired.
  expire: move the ask to expired with no answer recorded; a plain
    timeout, not a decision.
  hold: leave the ask exactly as it is; a later escalation step (not in
    this sub-task's scope) decides what happens to a held, overdue ask.

Only asks with a deadline_at in the past and a non-terminal state are
swept at expires_at (deadline_at, or the default lifetime when the caller
gave none; HCA-P19).
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from . import models
from .requests_api import TERMINAL_STATES, _audit_to_answer_record
from . import events
from . import webhooks

AUTO_DENY_VALUE = {"decision": "deny", "reason": "auto_deny: deadline passed with no answer"}
SWEPT_BY = "system:expiry_sweep"


def expire_ask(db: Session, ask_id: str, now: datetime = None, *, commit: bool = True) -> Optional[dict]:
    """Apply the ask's expiry policy to one ask, under a row lock, if it is
    still non-terminal and overdue. Returns the result dict ({"request_id",
    "expiry_policy", "action"}) or None when there was nothing to do (already
    answered, cancelled, expired, or not yet due). Called by the leased
    expire job (app/jobs.py, HCA-P20 Day 3) and by run_expiry_sweep."""
    now = now or datetime.now(timezone.utc)
    now_naive = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    # HCA-P19 Day 2: read under a row lock so an answer or cancel that landed
    # since the caller looked wins and the expiry skips.
    ask = db.execute(select(models.Ask).where(models.Ask.id == ask_id).with_for_update()).scalar_one_or_none()
    if ask is None or ask.state in TERMINAL_STATES or ask.expires_at is None or (ask.expires_at.replace(tzinfo=None) if ask.expires_at.tzinfo else ask.expires_at) > now_naive:
        if commit:
            db.rollback()
        return None
    if ask.expiry_policy == "hold":
        if commit:
            db.rollback()
        return {"request_id": ask.id, "expiry_policy": "hold", "action": "held"}

    if ask.expiry_policy == "auto_deny":
        row = models.AuditRow(
            tenant_id=ask.tenant_id,
            ask_id=ask.id,
            control=ask.control,
            value=dict(AUTO_DENY_VALUE),
            answered_by=SWEPT_BY,
            answered_role="system",
            answered_at=now,
            surface="web",
            device=None,
            path_taken=None,
            duration_ms=None,
            voice_used=None,
            passthrough={"swept_at": now.isoformat(), "deadline_at": ask.deadline_at.isoformat() if ask.deadline_at else None,
                         "expires_at": ask.expires_at.isoformat()},
        )
        db.add(row)
        ask.state = "expired"
        db.flush()
        record = _audit_to_answer_record(row)
        webhooks.enqueue(db, ask, "request.expired", ask.state, record, now)
        events.enqueue(db, "request.expired", record, tenant_id=ask.tenant_id)
        if commit:
            db.commit()
            events.wake_all()
        return {"request_id": ask.id, "expiry_policy": "auto_deny", "action": "auto_denied"}

    # expiry_policy == "expire": plain timeout, no answer recorded.
    ask.state = "expired"
    webhooks.enqueue(db, ask, "request.expired", ask.state, None, now)
    events.enqueue(db, "request.expired", {"request_id": ask.id, "state": "expired"}, tenant_id=ask.tenant_id)
    if commit:
        db.commit()
        events.wake_all()
    return {"request_id": ask.id, "expiry_policy": "expire", "action": "expired"}


def run_expiry_sweep(db: Session, now: datetime = None) -> List[dict]:
    """Expire every overdue, non-terminal ask (the direct, unleased path kept
    for tests and one-off repair; the service runs expiry through leased
    jobs, app/jobs.py). Returns one result dict per ask that was changed or
    held, in the shape {"request_id", "expiry_policy", "action"}."""
    now = now or datetime.now(timezone.utc)

    # HCA-P19: expires_at is always set on create (deadline_at or the default
    # lifetime), so every ask eventually leaves the inbox. Rows created before
    # P19 with no deadline have no expires_at and are left alone as before.
    overdue = db.execute(
        select(models.Ask.id).where(
            models.Ask.state.notin_(TERMINAL_STATES),
            models.Ask.expires_at.is_not(None),
            models.Ask.expires_at <= now,
        )
    ).scalars().all()

    results: List[dict] = []
    for ask_id in overdue:
        r = expire_ask(db, ask_id, now)
        if r is not None:
            results.append(r)
    return results
