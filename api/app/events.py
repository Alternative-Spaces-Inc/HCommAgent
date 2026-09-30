import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict

_subscribers: set[asyncio.Queue] = set()

VALID_EVENTS = {"request.created", "request.answered", "request.cancelled", "request.expired"}


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if event not in VALID_EVENTS:
        raise ValueError(f"unknown inbox stream event: {event}")
    envelope = {"event": event, "data": data, "timestamp": datetime.now(timezone.utc).isoformat()}
    for q in list(_subscribers):
        try:
            q.put_nowait(envelope)
        except asyncio.QueueFull:
            pass
    return envelope


def format_sse(envelope: Dict[str, Any]) -> str:
    return f"event: {envelope['event']}\ndata: {json.dumps(envelope, default=str)}\n\n"
