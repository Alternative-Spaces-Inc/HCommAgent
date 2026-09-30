import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from . import models
from .db import get_db
from .events import subscribe, unsubscribe, format_sse
from .requests_api import _ask_to_collection_request
from .auth import require_device

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
def list_inbox(db: Session = Depends(get_db), device: models.Device = Depends(require_device)):
    asks = db.execute(select(models.Ask).where(models.Ask.state.in_(PENDING_STATES))).scalars().all()
    ordered = sorted(asks, key=_sort_key)
    return [_ask_to_collection_request(a) for a in ordered]


async def inbox_event_source(keepalive_seconds: float = STREAM_KEEPALIVE_SECONDS):
    """The SSE body generator for GET /v1/inbox/stream, factored out so tests
    can drive it directly with asyncio.wait_for instead of going through an
    HTTP client's streaming transport (TestClient does not stream an
    open-ended body incrementally; see tests/test_inbox_api.py for the note
    on why the HTTP layer is not exercised end-to-end for this route)."""
    q = subscribe()
    try:
        yield ": connected\n\n"
        while True:
            try:
                envelope = await asyncio.wait_for(q.get(), timeout=keepalive_seconds)
                yield format_sse(envelope)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        unsubscribe(q)


@router.get("/stream")
async def stream_inbox(device: models.Device = Depends(require_device)):
    return StreamingResponse(inbox_event_source(), media_type="text/event-stream")
