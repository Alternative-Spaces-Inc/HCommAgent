"""Inbox event bus: durable rows first, in-process delivery second (HCA-P20 Day 1).

Before P20 every envelope lived only in the asyncio queues of the instance
that published it: a stream that reconnected, or an instance that restarted,
lost whatever happened in between, and the deployment was pinned to one
Cloud Run instance. Now publish() writes an events row before any queue
sees the envelope, the row id becomes the SSE id, and a reconnecting stream
hands back the last id it saw (SSE Last-Event-ID) to receive the rows it
missed through replay() before live delivery resumes.

Authorization stays on the server, as P19 Day 1 set it: a subscriber is
bound to an authenticated device and tenant at subscribe time, and both
publish() and replay() release an envelope only when the tenant matches
and, for ask-bearing events, the device is in the routing set stored with
the row. data.visible_to is still carried for the surface ladder UX but is
not a security control.

Replay window (owner selection 2, docs/decisions/DECISIONS_20261010.md
decision 6): 24 hours or 1,000 rows per tenant, tunable by environment. A
device that was away longer gets a shorter replay and falls back to the full
inbox read every client performs on connect.

Still single instance for fan-out: Day 2 adds Postgres LISTEN/NOTIFY with a
poll fallback so an envelope written by one instance wakes the streams held
by another. The module contract (subscribe, unsubscribe, publish, replay,
format_sse) does not change for that.
"""

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

VALID_EVENTS = {"request.created", "request.escalated", "request.answered", "request.cancelled", "request.expired"}

QUEUE_MAXSIZE = 100

REPLAY_WINDOW_SECONDS = int(os.environ.get("HCA_EVENT_REPLAY_WINDOW_SECONDS", str(24 * 3600)))
REPLAY_MAX_EVENTS = int(os.environ.get("HCA_EVENT_REPLAY_MAX_EVENTS", "1000"))


@dataclass(frozen=True)
class Subscriber:
    queue: asyncio.Queue
    device_id: str
    tenant_id: str


_subscribers: dict[asyncio.Queue, Subscriber] = {}

# Observability for P20: envelopes refused by a full queue are counted per
# device instead of vanishing. Read by tests and by the metrics work in P25.
dropped: dict[str, int] = {}


def subscribe(device_id: str, tenant_id: str) -> asyncio.Queue:
    if not device_id or not tenant_id:
        raise ValueError("a subscriber needs an authenticated device id and tenant id")
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    _subscribers[q] = Subscriber(queue=q, device_id=device_id, tenant_id=tenant_id)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.pop(q, None)


def subscriber_count() -> int:
    return len(_subscribers)


def _naive_utc(dt: datetime) -> datetime:
    """Columns are naive UTC (the P19 migrations declare DateTime without a
    zone); compare and store in that shape."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _store(event: str, data: Dict[str, Any], tenant_id: str, device_ids: Optional[List[str]], now: datetime,
           session_factory) -> int:
    from . import models
    with session_factory() as db:
        row = models.Event(tenant_id=tenant_id, event=event, data=data, device_ids=device_ids,
                           created_at=_naive_utc(now))
        db.add(row)
        db.commit()
        return int(row.id)


def _envelope(event_id: int, event: str, data: Dict[str, Any], created_at: datetime) -> Dict[str, Any]:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {"id": event_id, "event": event, "data": data, "timestamp": created_at.isoformat()}


def publish(event: str, data: Dict[str, Any], *, tenant_id: str, device_ids: Optional[Iterable[str]] = None,
            session_factory=None) -> Dict[str, Any]:
    """Write the events row, then enqueue for the tenant's subscribers; when
    device_ids is given, only for those devices (the routing set of a created
    or escalated ask). Terminal events (answered, cancelled, expired) pass
    device_ids=None and reach every device of the tenant so clients can clear
    their banners. The write comes first on purpose: a failed write raises
    before any client has seen the envelope, so what a client sees is always
    something it can replay from."""
    if event not in VALID_EVENTS:
        raise ValueError(f"unknown inbox stream event: {event}")
    if not tenant_id:
        raise ValueError("publish needs the ask's tenant id")
    if session_factory is None:
        from .db import SessionLocal
        session_factory = SessionLocal
    allowed = None if device_ids is None else sorted(set(device_ids))
    # The payload is made JSON-safe once, here, so the stored row, the live
    # envelope and a replayed envelope are byte-identical on the wire
    # (datetimes as ISO 8601, the shape FastAPI gives the HTTP responses;
    # before P20 the stream wrote str(datetime), a space-separated form).
    data = jsonable_encoder(data)
    now = datetime.now(timezone.utc)
    event_id = _store(event, data, tenant_id, allowed, now, session_factory)
    envelope = _envelope(event_id, event, data, now)
    allowed_set = None if allowed is None else set(allowed)
    for sub in list(_subscribers.values()):
        if sub.tenant_id != tenant_id:
            continue
        if allowed_set is not None and sub.device_id not in allowed_set:
            continue
        try:
            sub.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            dropped[sub.device_id] = dropped.get(sub.device_id, 0) + 1
    return envelope


def replay(db, *, tenant_id: str, device_id: str, after_id: int, now: Optional[datetime] = None,
           window_seconds: int = REPLAY_WINDOW_SECONDS, max_events: int = REPLAY_MAX_EVENTS) -> List[Dict[str, Any]]:
    """Envelopes the device missed: rows of its tenant with id greater than
    after_id, no older than the window, addressed to every device or to this
    one, oldest first, at most max_events. Same authorization rule as
    publish(), applied again here from the stored routing set, so a device
    cannot widen what it sees by sending a small Last-Event-ID."""
    from . import models
    now = now or datetime.now(timezone.utc)
    floor = _naive_utc(now) - timedelta(seconds=window_seconds)
    stmt = (select(models.Event)
            .where(models.Event.tenant_id == tenant_id)
            .where(models.Event.id > after_id)
            .where(models.Event.created_at >= floor)
            .order_by(models.Event.id.asc()))
    out: List[Dict[str, Any]] = []
    for row in db.execute(stmt).scalars():
        if row.device_ids is not None and device_id not in row.device_ids:
            continue
        out.append(_envelope(int(row.id), row.event, row.data, row.created_at))
        if len(out) >= max_events:
            break
    return out


def parse_last_event_id(value: Optional[str]) -> Optional[int]:
    """SSE Last-Event-ID is an opaque string on the wire; ours is the row id.
    Anything that is not a non-negative integer is treated as absent, so a
    client from before P20 (or a tampered header) simply gets no replay."""
    if value is None:
        return None
    value = value.strip()
    if not value.isdigit():
        return None
    return int(value)


def format_sse(envelope: Dict[str, Any]) -> str:
    head = f"id: {envelope['id']}\n" if envelope.get("id") is not None else ""
    return f"{head}event: {envelope['event']}\ndata: {json.dumps(envelope, default=str)}\n\n"
