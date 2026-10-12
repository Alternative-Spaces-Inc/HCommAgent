"""Inbox event bus: durable rows, table-driven fan-out, Postgres wake-ups (HCA-P20).

Day 1 made every envelope an events row before any client saw it and gave
GET /v1/inbox/stream Last-Event-ID replay. Day 2 makes delivery itself go
through the table so several API instances share one stream of truth:

  enqueue()      writes the event and Postgres pg_notify in the business
                 transaction. Callers commit state and event together.
  publish()      standalone event writer, using the same ordered insertion
                 and post-commit wake path.
  dispatcher     one asyncio task per event loop that holds subscribers; on
                 a wake, or every POLL_SECONDS without one, it reads rows
                 above its cursor and hands each to the matching local
                 queues. Local and remote publishes take the same path, so
                 nothing is delivered twice and nothing depends on which
                 instance wrote the row.
  listener       one thread per process holding a detached Postgres
                 connection on LISTEN hca_events; every notification wakes
                 the dispatchers. The poll is the fallback when the listener
                 is down or a notification is lost (owner selection 1:
                 LISTEN/NOTIFY with a 2 s poll fallback).

SQLite (unit tests, local runs) has no NOTIFY: publish() wakes the in-process
dispatchers directly and the poll covers the rest, so the same tests run in
both modes (owner selection 4). The CI Postgres job proves the real listener.

Authorization is unchanged from P19 Day 1: a subscriber is bound to an
authenticated device and tenant at subscribe time, and a row reaches a queue
only when the tenant matches and, for ask-bearing events, the device is in
the routing set stored with the row. The same rule is applied by replay().
"""

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select, text

log = logging.getLogger("hca.events")

VALID_EVENTS = {"request.created", "request.escalated", "request.answered", "request.cancelled", "request.expired"}

QUEUE_MAXSIZE = 100

REPLAY_WINDOW_SECONDS = int(os.environ.get("HCA_EVENT_REPLAY_WINDOW_SECONDS", str(24 * 3600)))
REPLAY_MAX_EVENTS = int(os.environ.get("HCA_EVENT_REPLAY_MAX_EVENTS", "1000"))
POLL_SECONDS = float(os.environ.get("HCA_EVENT_POLL_SECONDS", "2"))
# HCA-P20 Day 5: open streams per device. The live metrics page showed 46
# streams from one extension device on one instance: each alarm tick opened
# a new fetch while the proxy kept the old ones alive. Beyond this many, the
# oldest stream of the device is told to close (event stream.closed, reason
# superseded) and the client keeps its newest one.
STREAM_MAX_PER_DEVICE = int(os.environ.get("HCA_STREAM_MAX_PER_DEVICE", "4"))
SUPERSEDED_ENVELOPE = {"event": "stream.closed", "data": {"reason": "superseded"}, "id": None}
NOTIFY_CHANNEL = "hca_events"


@dataclass
class Subscriber:
    queue: asyncio.Queue
    device_id: str
    tenant_id: str
    loop: Optional[asyncio.AbstractEventLoop]
    closed: bool = False
    reset_required: bool = False


@dataclass
class Dispatcher:
    loop: asyncio.AbstractEventLoop
    wake: asyncio.Event
    cursor: int
    task: Optional[asyncio.Task] = None
    stats: Dict[str, int] = field(default_factory=lambda: {"wakes": 0, "polls": 0, "dispatched": 0})


_subscribers: dict[asyncio.Queue, Subscriber] = {}
_dispatchers: dict[asyncio.AbstractEventLoop, Dispatcher] = {}
_lock = threading.Lock()

# Observability: envelopes refused by a full queue are counted per device
# instead of vanishing (read by tests and the Day 4 metrics).
dropped: dict[str, int] = {}

# HCA-P20 Day 4: the last event id each open stream has written to its
# client (keyed by its queue), so stream lag can be measured against the
# newest events row (app/metrics.py). Replay counters for the same page.
_last_sent: dict[asyncio.Queue, int] = {}
replay_state: Dict[str, int] = {"replays": 0, "replayed_events": 0, "gaps": 0, "truncated": 0}
stream_state: Dict[str, int] = {"superseded": 0}

# Listener state for /healthz and the Day 4 metrics.
listener_state: Dict[str, Any] = {"running": False, "connected": False, "notifications": 0, "errors": 0, "last_error": None}
_listener_thread: Optional[threading.Thread] = None
_listener_stop = threading.Event()


def _session_factory():
    from .db import SessionLocal
    return SessionLocal


# ---------------------------------------------------------------- subscribers

def subscribe(device_id: str, tenant_id: str) -> asyncio.Queue:
    """Register a queue for an authenticated device. Must be called from a
    running event loop: the dispatcher that feeds the queue lives on it."""
    if not device_id or not tenant_id:
        raise ValueError("a subscriber needs an authenticated device id and tenant id")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    evict: List[Subscriber] = []
    with _lock:
        mine = [s for s in _subscribers.values() if s.device_id == device_id and not s.closed]
        while len(mine) >= STREAM_MAX_PER_DEVICE:
            oldest = mine.pop(0)  # _subscribers keeps insertion order
            oldest.closed = True
            evict.append(oldest)
            stream_state["superseded"] += 1
        _subscribers[q] = Subscriber(queue=q, device_id=device_id, tenant_id=tenant_id, loop=loop)
    for old in evict:
        try:
            old.queue.put_nowait(dict(SUPERSEDED_ENVELOPE))
        except asyncio.QueueFull:
            pass  # the generator also checks is_closed on every keep-alive tick
    if loop is not None:
        d = ensure_dispatcher(loop)
        with _lock:
            # Lag is measured from here: a stream that connects after the
            # newest row has nothing to send and must not read as behind.
            _last_sent[q] = d.cursor
    return q


def is_closed(q: asyncio.Queue) -> bool:
    """True once subscribe() superseded this stream for a newer one of the same device."""
    with _lock:
        s = _subscribers.get(q)
        return bool(s and s.closed)


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        _subscribers.pop(q, None)
        _last_sent.pop(q, None)


def mark_sent(q: asyncio.Queue, event_id: Optional[int]) -> None:
    """The stream generator reports each id it has written to the client."""
    if event_id is None:
        return
    with _lock:
        if q in _subscribers:
            _last_sent[q] = max(_last_sent.get(q, 0), int(event_id))


def stream_positions() -> List[Dict[str, Any]]:
    """One row per open stream: device, tenant, last id written (0 when none yet)."""
    with _lock:
        return [{"device_id": s.device_id, "tenant_id": s.tenant_id, "last_sent_id": _last_sent.get(q, 0)}
                for q, s in _subscribers.items()]


def subscriber_count() -> int:
    return len(_subscribers)


# ----------------------------------------------------------------- the table

def _naive_utc(dt: datetime) -> datetime:
    """Columns are naive UTC (the P19 migrations declare DateTime without a
    zone); compare and store in that shape."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _envelope(event_id: int, event: str, data: Dict[str, Any], created_at: datetime) -> Dict[str, Any]:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {"id": event_id, "event": event, "data": data, "timestamp": created_at.isoformat()}


def _is_postgres(db) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


# All event writers take this transaction lock before allocating a sequence
# id. PostgreSQL sequences alone do not order commits: without this lock a
# delayed lower-id commit can be lost by both live and replay cursors.
EVENT_INSERT_LOCK = 0x48434145564E54


def enqueue(db, event: str, data: Dict[str, Any], *, tenant_id: str,
            device_ids: Optional[Iterable[str]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Append an event inside the state-changing transaction; caller commits.

    On PostgreSQL the advisory lock serializes id allocation and commits.
    SQLite's single writer supplies the same ordering. NOTIFY is emitted by
    PostgreSQL only after commit; local dispatchers also poll for committed
    rows, so rolled-back state can never reach a subscriber.
    """
    from . import models
    if event not in VALID_EVENTS:
        raise ValueError(f"unknown inbox stream event: {event}")
    if not tenant_id:
        raise ValueError("publish needs the ask's tenant id")
    allowed = None if device_ids is None else sorted(set(device_ids))
    data = jsonable_encoder(data)
    now = now or datetime.now(timezone.utc)
    if _is_postgres(db):
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": EVENT_INSERT_LOCK})
    row = models.Event(tenant_id=tenant_id, event=event, data=data, device_ids=allowed,
                       created_at=_naive_utc(now))
    db.add(row)
    db.flush()
    if _is_postgres(db):
        db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": NOTIFY_CHANNEL, "payload": str(row.id)})
    return _envelope(int(row.id), event, data, now)


def max_event_id(session_factory=None) -> int:
    from . import models
    session_factory = session_factory or _session_factory()
    with session_factory() as db:
        return int(db.execute(select(func.max(models.Event.id))).scalar() or 0)


def _fetch_after(cursor: int, limit: int = 500):
    from . import models
    with _session_factory()() as db:
        rows = db.execute(select(models.Event).where(models.Event.id > cursor)
                          .order_by(models.Event.id.asc()).limit(limit)).scalars().all()
        return [(int(r.id), r.tenant_id, r.event, r.data, r.device_ids, r.created_at) for r in rows]


def publish(event: str, data: Dict[str, Any], *, tenant_id: str, device_ids: Optional[Iterable[str]] = None,
            session_factory=None) -> Dict[str, Any]:
    """Write the events row, then wake the dispatchers. When device_ids is
    given, only those devices (the routing set of a created or escalated ask)
    receive it; terminal events (answered, cancelled, expired) pass
    device_ids=None and reach every device of the tenant so clients can clear
    their banners. The write comes first on purpose: a failed write raises
    before any client has seen the envelope, so what a client sees is always
    something it can replay from. Safe to call from any thread."""
    session_factory = session_factory or _session_factory()
    with session_factory() as db:
        envelope = enqueue(db, event, data, tenant_id=tenant_id, device_ids=device_ids)
        db.commit()
    wake_all()
    return envelope


def _replay_rows(db, tenant_id, device_id, after_id, now, window_seconds, max_events):
    from . import models
    floor = _naive_utc(now) - timedelta(seconds=window_seconds)
    # Bound scanning as well as output. If a busy tenant's routing records
    # exceed this budget, explicitly ask the client to reconcile its inbox.
    rows = db.execute(select(models.Event).where(
        models.Event.tenant_id == tenant_id, models.Event.id > after_id,
        models.Event.created_at >= floor,
    ).order_by(models.Event.id).limit(max_events + 1)).scalars().all()
    truncated = len(rows) > max_events
    envelopes = [_envelope(int(r.id), r.event, r.data, r.created_at) for r in rows[:max_events]
                 if r.device_ids is None or device_id in r.device_ids]
    aged_out = db.execute(select(models.Event.id).where(
        models.Event.tenant_id == tenant_id, models.Event.id > after_id,
        models.Event.created_at < floor,
    ).limit(1)).first() is not None
    latest = db.execute(select(func.max(models.Event.id))).scalar() or 0
    oldest = db.execute(select(func.min(models.Event.id)).where(models.Event.tenant_id == tenant_id)).scalar()
    removed = after_id > 0 and (oldest is None or after_id < oldest - 1)
    gap = "cursor_ahead" if after_id > latest else "replay_limit" if truncated else "replay_expired" if aged_out or removed else None
    return envelopes, gap


def replay_gap(db, *, tenant_id: str, device_id: str, after_id: int, now: Optional[datetime] = None,
               window_seconds: int = REPLAY_WINDOW_SECONDS, max_events: int = REPLAY_MAX_EVENTS) -> Optional[str]:
    """Reason a reconnect must poll its current inbox instead of trusting a partial log."""
    return _replay_rows(db, tenant_id, device_id, after_id, now or datetime.now(timezone.utc),
                        window_seconds, max_events)[1]


def replay(db, *, tenant_id: str, device_id: str, after_id: int, now: Optional[datetime] = None,
           window_seconds: int = REPLAY_WINDOW_SECONDS, max_events: int = REPLAY_MAX_EVENTS) -> List[Dict[str, Any]]:
    """Bounded replay of authorized rows; replay_gap exposes required reconciliation."""
    out, gap = _replay_rows(db, tenant_id, device_id, after_id, now or datetime.now(timezone.utc),
                            window_seconds, max_events)
    replay_state["replays"] += 1
    replay_state["replayed_events"] += len(out)
    if gap == "replay_limit":
        replay_state["truncated"] += 1
    if gap:
        replay_state["gaps"] += 1
    return out


def parse_last_event_id(value: Optional[str]) -> Optional[int]:
    """SSE Last-Event-ID is an opaque string on the wire; ours is the row id.
    Anything that is not a non-negative integer is treated as absent, so a
    client from before P20 (or a tampered header) simply gets no replay."""
    if value is None:
        return None
    value = value.strip()
    if len(value) > 19 or not value.isascii() or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed <= 2**63 - 1 else None


def format_sse(envelope: Dict[str, Any]) -> str:
    head = f"id: {envelope['id']}\n" if envelope.get("id") is not None else ""
    return f"{head}event: {envelope['event']}\ndata: {json.dumps(envelope, default=str)}\n\n"


# --------------------------------------------------------------- dispatchers

def ensure_dispatcher(loop: asyncio.AbstractEventLoop) -> Dispatcher:
    """One dispatcher per event loop, started at the first subscribe. Its
    cursor starts at the current max id: older rows are replay territory."""
    with _lock:
        d = _dispatchers.get(loop)
        if d is not None:
            return d
        d = Dispatcher(loop=loop, wake=asyncio.Event(), cursor=max_event_id())
        _dispatchers[loop] = d
    d.task = loop.create_task(_dispatch_forever(d), name="hca-events-dispatcher")
    return d


def _deliver(d: Dispatcher, rows) -> None:
    with _lock:
        subs = [s for s in _subscribers.values() if s.loop is d.loop]
    for event_id, tenant_id, event, data, device_ids, created_at in rows:
        allowed = None if device_ids is None else set(device_ids)
        envelope = _envelope(event_id, event, data, created_at)
        for sub in subs:
            if sub.tenant_id != tenant_id:
                continue
            if allowed is not None and sub.device_id not in allowed:
                continue
            if sub.closed:
                if sub.reset_required:
                    dropped[sub.device_id] = dropped.get(sub.device_id, 0) + 1
                continue
            try:
                sub.queue.put_nowait(envelope)
                d.stats["dispatched"] += 1
            except asyncio.QueueFull:
                # Count both the refused event and the queued event evicted
                # to make room for the recovery instruction.
                dropped[sub.device_id] = dropped.get(sub.device_id, 0) + 2
                # Make the loss visible. Replace one queued event with a
                # reset instruction and close the stream after that notice.
                sub.closed = True
                sub.reset_required = True
                sub.queue.get_nowait()
                sub.queue.put_nowait({"event": "stream.reset", "data": {"reason": "queue_overflow"}, "id": None})
        d.cursor = max(d.cursor, event_id)


async def _dispatch_forever(d: Dispatcher) -> None:
    try:
        while True:
            try:
                await asyncio.wait_for(d.wake.wait(), timeout=POLL_SECONDS)
                d.stats["wakes"] += 1
            except asyncio.TimeoutError:
                d.stats["polls"] += 1
            d.wake.clear()
            try:
                rows = await asyncio.to_thread(_fetch_after, d.cursor)
                while rows:
                    _deliver(d, rows)
                    rows = await asyncio.to_thread(_fetch_after, d.cursor) if len(rows) >= 500 else []
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the next poll retries; a stream must not die on one bad read
                log.warning("events dispatcher read failed: %s", exc)
                await asyncio.sleep(0.5)
    finally:
        with _lock:
            if _dispatchers.get(d.loop) is d:
                del _dispatchers[d.loop]


def wake_all() -> None:
    """Signal every live dispatcher from any thread; dispatchers of closed
    loops (tests run many short loops) are dropped here."""
    with _lock:
        items = list(_dispatchers.items())
    for loop, d in items:
        if loop.is_closed():
            with _lock:
                _dispatchers.pop(loop, None)
            continue
        try:
            loop.call_soon_threadsafe(d.wake.set)
        except RuntimeError:
            with _lock:
                _dispatchers.pop(loop, None)


def dispatcher_stats() -> Dict[str, Any]:
    with _lock:
        ds = list(_dispatchers.values())
    return {
        "dispatchers": len(ds),
        "subscribers": len(_subscribers),
        "wakes": sum(d.stats["wakes"] for d in ds),
        "polls": sum(d.stats["polls"] for d in ds),
        "dispatched": sum(d.stats["dispatched"] for d in ds),
        "dropped": sum(dropped.values()),
        "listener": dict(listener_state),
    }


# ------------------------------------------------------------------ listener

def _listen_forever(engine, stop: threading.Event) -> None:
    import select as _select
    backoff = 1.0
    while not stop.is_set():
        raw = None
        try:
            # A pooled connection taken out of the pool for good: the pool
            # keeps its small bound for requests and the listener holds one
            # extra slot per process (3 instances plus the pool stays well
            # under the instance's connection limit).
            raw = engine.raw_connection()
            raw.detach()
            pg = raw.dbapi_connection
            pg.set_isolation_level(0)  # autocommit: LISTEN takes effect at once
            cur = pg.cursor()
            cur.execute(f"LISTEN {NOTIFY_CHANNEL}")
            listener_state["connected"] = True
            backoff = 1.0
            while not stop.is_set():
                if _select.select([pg], [], [], 1.0) == ([], [], []):
                    continue
                pg.poll()
                n = 0
                while pg.notifies:
                    pg.notifies.pop(0)
                    n += 1
                if n:
                    listener_state["notifications"] += n
                    wake_all()
        except Exception as exc:
            listener_state["connected"] = False
            listener_state["errors"] += 1
            listener_state["last_error"] = str(exc)[:200]
            log.warning("events listener lost its connection (%s); poll fallback covers delivery, retry in %.0fs", exc, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            listener_state["connected"] = False
            if raw is not None:
                try:
                    raw.close()
                except Exception:
                    pass


def start_listener(engine=None) -> bool:
    """Start the LISTEN thread when the database is Postgres; False (and no
    thread) on SQLite, where publish() wakes the dispatchers directly."""
    global _listener_thread
    if engine is None:
        from .db import engine as _engine
        engine = _engine
    if engine.dialect.name != "postgresql":
        return False
    with _lock:
        if _listener_thread is not None and _listener_thread.is_alive():
            return True
        _listener_stop.clear()
        _listener_thread = threading.Thread(target=_listen_forever, args=(engine, _listener_stop),
                                            name="hca-events-listener", daemon=True)
        listener_state["running"] = True
        _listener_thread.start()
    return True


def stop_listener(timeout: float = 5.0) -> None:
    global _listener_thread
    _listener_stop.set()
    t = _listener_thread
    if t is not None:
        t.join(timeout)
    _listener_thread = None
    listener_state["running"] = False
    listener_state["connected"] = False


def wait_listener_connected(timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if listener_state["connected"]:
            return True
        time.sleep(0.05)
    return bool(listener_state["connected"])
