"""In-process inbox event bus with server side authorization (HCA-P19 Day 1).

Before P19 every envelope went to every subscriber queue and clients were
asked to ignore envelopes whose data.visible_to did not name them. That
list is still carried for the surface ladder UX, but it is no longer a
security control: a subscriber is bound to an authenticated device and
tenant at subscribe time, and publish() enqueues an envelope only when the
subscriber's tenant matches and, for ask-bearing events, the device is in
the routing set computed on the server.

Still in process and single instance; P20 replaces the queue set with a
durable events table and Last-Event-ID replay without changing this
module's contract (subscribe, unsubscribe, publish, format_sse).
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

VALID_EVENTS = {"request.created", "request.escalated", "request.answered", "request.cancelled", "request.expired"}

QUEUE_MAXSIZE = 100


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


def publish(event: str, data: Dict[str, Any], *, tenant_id: str, device_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Enqueue for the tenant's subscribers; when device_ids is given, only
    for those devices (the routing set of a created or escalated ask).
    Terminal events (answered, cancelled, expired) pass device_ids=None and
    reach every device of the tenant so clients can clear their banners."""
    if event not in VALID_EVENTS:
        raise ValueError(f"unknown inbox stream event: {event}")
    if not tenant_id:
        raise ValueError("publish needs the ask's tenant id")
    allowed = None if device_ids is None else set(device_ids)
    envelope = {"event": event, "data": data, "timestamp": datetime.now(timezone.utc).isoformat()}
    for sub in list(_subscribers.values()):
        if sub.tenant_id != tenant_id:
            continue
        if allowed is not None and sub.device_id not in allowed:
            continue
        try:
            sub.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            dropped[sub.device_id] = dropped.get(sub.device_id, 0) + 1
    return envelope


def format_sse(envelope: Dict[str, Any]) -> str:
    return f"event: {envelope['event']}\ndata: {json.dumps(envelope, default=str)}\n\n"
