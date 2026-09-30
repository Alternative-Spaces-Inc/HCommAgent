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
swept; an ask with no deadline_at never expires on its own.
"""

from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session
from sqlalchemy import select

from . import models
from .requests_api import TERMINAL_STATES, _audit_to_answer_record
from .events import publish

AUTO_DENY_VALUE = {"decision": "deny", "reason": "auto_deny: deadline passed with no answer"}
SWEPT_BY = "system:expiry_sweep"


def run_expiry_sweep(db: Session, now: datetime = None) -> List[dict]:
    """Sweep overdue, non-terminal asks. Returns one result dict per ask
    that was changed, in the shape {"request_id", "expiry_policy", "action"}
    where action is "auto_denied", "expired", or "held" (held asks are
    reported but left untouched, matching the hold policy above)."""
    now = now or datetime.now(timezone.utc)

    overdue = db.execute(
        select(models.Ask).where(
            models.Ask.state.notin_(TERMINAL_STATES),
            models.Ask.deadline_at.is_not(None),
            models.Ask.deadline_at < now,
        )
    ).scalars().all()

    results: List[dict] = []

    for ask in overdue:
        if ask.expiry_policy == "hold":
            results.append({"request_id": ask.id, "expiry_policy": "hold", "action": "held"})
            continue

        if ask.expiry_policy == "auto_deny":
            row = models.AuditRow(
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
                passthrough={"swept_at": now.isoformat(), "deadline_at": ask.deadline_at.isoformat()},
            )
            db.add(row)
            ask.state = "expired"
            db.commit()
            record = _audit_to_answer_record(row)
            publish("request.expired", record)
            results.append({"request_id": ask.id, "expiry_policy": "auto_deny", "action": "auto_denied"})
            continue

        # expiry_policy == "expire": plain timeout, no answer recorded.
        ask.state = "expired"
        db.commit()
        publish("request.expired", {"request_id": ask.id, "state": "expired"})
        results.append({"request_id": ask.id, "expiry_policy": "expire", "action": "expired"})

    return results
