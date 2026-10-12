import asyncio
import time

from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from . import models
from .db import get_db, SessionLocal
from .events import SUPERSEDED_ENVELOPE, is_closed, subscribe, unsubscribe, format_sse, replay, parse_last_event_id, mark_sent
from .requests_api import _ask_to_collection_request
from .schemas import InboxItem
from .auth import require_linked_device, google_link_required, device_is_authorized
from . import routing
from datetime import datetime, timezone

router = APIRouter(prefix="/v1/inbox", tags=["inbox"])

PENDING_STATES = ("queued", "shown")

STREAM_KEEPALIVE_SECONDS = 15


def _sort_key(ask: models.Ask):
    # Earliest deadline first, no-deadline last; among ties, a red gate (higher
    # impact) sorts before a plain ask.
    has_deadline = ask.deadline_at is None
    deadline = ask.deadline_at
    is_red_gate = 0 if ask.red_gate else 1
    return (has_deadline, deadline, is_red_gate)


@router.get("", response_model=list[InboxItem], responses={200: {"headers": {"X-HCA-Next-Cursor": {"description": "Pass as after to request the next page", "schema": {"type": "string"}}}}})
def list_inbox(response: Response, limit: int = Query(default=200, ge=1, le=500),
               after: str = Query(default="", max_length=100),
               db: Session = Depends(get_db), device: models.Device = Depends(require_linked_device)):
    asks = db.execute(select(models.Ask).where(models.Ask.state.in_(PENDING_STATES))
                      .where(models.Ask.tenant_id == device.tenant_id, models.Ask.id > after)
                      .order_by(models.Ask.id).limit(limit + 1)).scalars().all()
    if len(asks) > limit:
        asks = asks[:limit]
        response.headers["X-HCA-Next-Cursor"] = asks[-1].id
    # HCA-P14: a device sees an ask only once its rank's window has opened
    # (app/routing.py). The poll path applies the same rule as the stream.
    now = datetime.now(timezone.utc)
    link_required = google_link_required()
    asks = [a for a in asks if routing.is_visible(db, a, device, now, link_required)]
    ordered = sorted(asks, key=_sort_key)
    # Each inbox item carries its request_id as a sibling of the
    # CollectionRequest fields; the extension needs it to build the
    # POST /v1/requests/{request_id}/answer path (HCA-P6 proof found the
    # list without it, so the popup answered against "undefined").
    return [_ask_to_collection_request(a) | {"request_id": a.id, "expires_at": a.expires_at} for a in ordered]


def _device_still_authorized(device_id: str) -> bool:
    """Re-read the device row; a revoked device loses an open stream at the
    next keep-alive tick instead of keeping it until it disconnects."""
    with SessionLocal() as db:
        d = db.get(models.Device, device_id)
        return device_is_authorized(db, d)


def _replay_for(device: models.Device, after_id: int):
    with SessionLocal() as db:
        return replay(db, tenant_id=device.tenant_id, device_id=device.id, after_id=after_id)


async def inbox_event_source(device: models.Device, keepalive_seconds: float = STREAM_KEEPALIVE_SECONDS,
                             authorized_fn=_device_still_authorized, last_event_id: Optional[int] = None):
    """The SSE body generator for GET /v1/inbox/stream, factored out so tests
    can drive it directly with asyncio.wait_for instead of going through an
    HTTP client's streaming transport (TestClient does not stream an
    open-ended body incrementally; see tests/test_inbox_api.py for the note
    on why the HTTP layer is not exercised end-to-end for this route).

    HCA-P20 Day 1: with last_event_id (the SSE Last-Event-ID a reconnecting
    client sends back) the rows the device missed are replayed first, in id
    order, each with its id line. The live queue is subscribed before the
    replay query runs so nothing published in between is lost; an envelope
    that arrives both ways is sent once (its id is not above the replay high
    water mark)."""
    q = subscribe(device.id, device.tenant_id)
    try:
        if not await asyncio.to_thread(authorized_fn, device.id):
            yield 'event: stream.closed\ndata: {"reason":"authorization revoked"}\n\n'
            return
        next_auth_check = time.monotonic() + keepalive_seconds
        yield ": connected\n\n"
        high_water = -1
        if last_event_id is not None:
            def replay_gap():
                from .events import replay_gap
                with SessionLocal() as db:
                    return replay_gap(db, tenant_id=device.tenant_id, device_id=device.id, after_id=last_event_id)
            gap = await asyncio.to_thread(replay_gap)
            if gap:
                yield format_sse({"event": "stream.reset", "data": {"reason": gap}})
                return
            for envelope in await asyncio.to_thread(_replay_for, device, last_event_id):
                if time.monotonic() >= next_auth_check:
                    if not await asyncio.to_thread(authorized_fn, device.id):
                        yield 'event: stream.closed\ndata: {"reason":"authorization revoked"}\n\n'
                        return
                    next_auth_check = time.monotonic() + keepalive_seconds
                high_water = max(high_water, envelope["id"])
                mark_sent(q, envelope["id"])
                yield format_sse(envelope)
        while True:
            if time.monotonic() >= next_auth_check:
                if not await asyncio.to_thread(authorized_fn, device.id):
                    yield 'event: stream.closed\ndata: {"reason":"authorization revoked"}\n\n'
                    return
                next_auth_check = time.monotonic() + keepalive_seconds
            try:
                envelope = await asyncio.wait_for(q.get(), timeout=keepalive_seconds)
                if envelope.get("event") in {"stream.closed", "stream.reset"}:
                    # HCA-P20 Day 5: a newer stream of this device took our
                    # slot (STREAM_MAX_PER_DEVICE); tell the client and stop.
                    yield format_sse(envelope)
                    return
                if envelope.get("id") is not None and envelope["id"] <= high_water:
                    continue
                mark_sent(q, envelope.get("id"))
                yield format_sse(envelope)
            except asyncio.TimeoutError:
                if is_closed(q):
                    yield format_sse(dict(SUPERSEDED_ENVELOPE))
                    return
                if not await asyncio.to_thread(authorized_fn, device.id):
                    yield "event: stream.closed\ndata: {\"reason\": \"device revoked\"}\n\n"
                    return
                yield ": keep-alive\n\n"
    finally:
        unsubscribe(q)


@router.get("/stream")
async def stream_inbox(device: models.Device = Depends(require_linked_device),
                       last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
                       since: Optional[str] = Query(default=None, description="Last event id seen, for clients that cannot set the Last-Event-ID header")):
    # The browser EventSource API sets Last-Event-ID itself on reconnect; a
    # client that reconnects by hand (the Mac app's URLSession) may pass the
    # same value as ?since=. The header wins when both are present.
    last_event_id = parse_last_event_id(last_event_id_header if last_event_id_header is not None else since)
    return StreamingResponse(inbox_event_source(device, last_event_id=last_event_id), media_type="text/event-stream")
