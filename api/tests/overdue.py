"""HCA-P19: POST /v1/requests refuses a deadline in the past (422), so tests
that need an overdue ask create it with a future deadline and then move
deadline_at and expires_at into the past straight in the database, which is
exactly the state the sweep meets in production once time has passed."""

from datetime import datetime, timedelta, timezone

from app import models
from app.db import SessionLocal


def backdate(request_id: str, hours: float = 1.0) -> datetime:
    past = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(tzinfo=None)
    with SessionLocal() as db:
        ask = db.get(models.Ask, request_id)
        ask.deadline_at = past
        ask.expires_at = past
        db.commit()
    return past
