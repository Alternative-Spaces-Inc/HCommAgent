import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from . import models
from .db import get_db, SessionLocal
from .events import subscribe, unsubscribe, format_sse
from .requests_api import _ask_to_collection_request
from .auth import require_linked_device, google_link_required
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


@router.get("")
def list_inbox(db: Session = Depends(get_db), device: models.Device = Depends(require_linked_device)):
    asks = db.execute(select(models.Ask).where(models.Ask.state.in_(PENDING_STATES)).where(models.Ask.tenant_id == device.tenant_id)).scalars().all()
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
    return [_ask_to_collection_request(a) | {"request_id": a.id} for a in ordered]


def _device_still_authorized(device_id: str) -> bool:
    """Re-read the device row; a revoked device loses an open stream at the
    next keep-alive tick instead of keeping it until it disconnects."""
    with SessionLocal() as db:
        d = db.get(models.Device, device_id)
        return d is not None and d.revoked_at is None


async def inbox_event_source(device: models.Device, keepalive_seconds: float = STREAM_KEEPALIVE_SECONDS,
                             authorized_fn=_device_still_authorized):
    """The SSE body generator for GET /v1/inbox/stream, factored out so tests
    can drive it directly with asyncio.wait_for instead of going through an
    HTTP client's streaming transport (TestClient does not stream an
    open-ended body incrementally; see tests/test_inbox_api.py for the note
    on why the HTTP layer is not exercised end-to-end for this route)."""
    q = subscribe(device.id, device.tenant_id)
    try:
        yield ": connected\n\n"
        while True:
            try:
                envelope = await asyncio.wait_for(q.get(), timeout=keepalive_seconds)
                yield format_sse(envelope)
            except asyncio.TimeoutError:
                if not await asyncio.to_thread(authorized_fn, device.id):
                    yield "event: stream.closed\ndata: {\"reason\": \"device revoked\"}\n\n"
                    return
                yield ": keep-alive\n\n"
    finally:
        unsubscribe(q)


@router.get("/stream")
async def stream_inbox(device: models.Device = Depends(require_linked_device)):
    return StreamingResponse(inbox_event_source(device), media_type="text/event-stream")
