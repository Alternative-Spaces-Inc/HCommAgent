"""Database-backed signature replay guard (HCA-P20 Day 2).

app/devicesig.py keeps an in-memory ReplayGuard: fast, but per process. With
more than one API instance a signed request could be replayed against a
second instance inside the skew window, and a restart forgot everything.
This module records each (device, nonce) pair in the nonces table in its own
short transaction; the composite primary key makes the second insert fail,
whichever instance runs it. The in-memory guard stays in front as the cheap
first check; this is the one that holds across instances and restarts.

Rows older than the full nonce retention interval (601 seconds) are
deleted opportunistically every PRUNE_EVERY calls. This covers both ends
of the permitted signature timestamp skew.
"""

import itertools
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from . import devicesig

PRUNE_EVERY = 256
_calls = itertools.count(1)
_lock = threading.Lock()


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def check_and_record(device_id: str, nonce: str, now: Optional[datetime] = None, session_factory=None,
                     ttl_seconds: int = devicesig.NONCE_RETENTION_SECONDS) -> bool:
    """True when the pair is new (and now recorded); False on a replay."""
    from . import models
    if session_factory is None:
        from .db import SessionLocal
        session_factory = SessionLocal
    now = _naive(now or datetime.now(timezone.utc))
    with session_factory() as db:
        with _lock:
            n = next(_calls)
        if n % PRUNE_EVERY == 0:
            db.execute(delete(models.Nonce).where(models.Nonce.seen_at < now - timedelta(seconds=ttl_seconds)))
            db.commit()
        db.add(models.Nonce(device_id=device_id, nonce=nonce, seen_at=now))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return False
        return True


def prune(session_factory=None, ttl_seconds: int = devicesig.NONCE_RETENTION_SECONDS, now: Optional[datetime] = None) -> int:
    from . import models
    if session_factory is None:
        from .db import SessionLocal
        session_factory = SessionLocal
    now = _naive(now or datetime.now(timezone.utc))
    with session_factory() as db:
        res = db.execute(delete(models.Nonce).where(models.Nonce.seen_at < now - timedelta(seconds=ttl_seconds)))
        db.commit()
        return int(res.rowcount or 0)
