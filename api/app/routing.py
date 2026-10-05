"""Surface routing (HCA-P14, docs/plans/HCA_P14_SURFACE_ROUTING_v1.md).

The rule lives here and nowhere else. Every linked, unrevoked device that
the server has seen recently is ranked by its priority (lower first;
equal values share a rank). An ask is visible to a device of rank r from
created_at + r * window, where window is HCA_SURFACE_FALLBACK_SECONDS
(default 90) and half that for a red gate. Rank 0 sees the ask at once.

GET /v1/inbox applies the rule for the calling device, so the clients'
60 s poll is the fallback that needs no timer. Stream envelopes for
request.created and request.escalated carry data.visible_to, the device
ids the event applies to at that moment; a client ignores envelopes that
do not name it. request.escalated is published by an in-process timer
armed at ask creation for every later rank; if the instance is recycled
before it fires, the poll covers the gap.

With every device at the default priority there is one rank and the
behaviour is exactly pre-P14: every device sees every ask immediately.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

DEFAULT_FALLBACK_SECONDS = 90.0
DEFAULT_ACTIVE_SECONDS = 600.0


def fallback_seconds() -> float:
    return float(os.environ.get("HCA_SURFACE_FALLBACK_SECONDS", DEFAULT_FALLBACK_SECONDS))


def active_seconds() -> float:
    return float(os.environ.get("HCA_DEVICE_ACTIVE_SECONDS", DEFAULT_ACTIVE_SECONDS))


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def window_seconds(red_gate: bool, base: Optional[float] = None) -> float:
    base = fallback_seconds() if base is None else base
    return base / 2.0 if red_gate else base


def eligible_devices(db: Session, now: datetime, link_required: bool) -> List[models.Device]:
    """Unrevoked devices seen within the active window, linked when the gate is on."""
    cutoff = now - timedelta(seconds=active_seconds())
    rows = db.execute(select(models.Device).where(models.Device.revoked_at.is_(None))).scalars().all()
    out = []
    for d in rows:
        seen = _aware(d.last_seen_at)
        if seen is None or seen < cutoff:
            continue
        if link_required and not d.google_sub:
            continue
        out.append(d)
    return out


def rank_of(device: models.Device, eligible: Iterable[models.Device]) -> int:
    """Number of distinct priorities strictly lower than the device's own."""
    return len({d.priority for d in eligible if d.priority < device.priority})


def rank_count(eligible: Iterable[models.Device]) -> int:
    return len({d.priority for d in eligible})


def visible_from(created_at: datetime, rank: int, red_gate: bool) -> datetime:
    return _aware(created_at) + timedelta(seconds=rank * window_seconds(red_gate))


def visible_to(db: Session, ask: models.Ask, now: datetime, link_required: bool) -> List[str]:
    """Device ids that may see the ask at `now`, in ladder order."""
    eligible = eligible_devices(db, now, link_required)
    out = []
    for d in sorted(eligible, key=lambda d: (d.priority, d.created_at)):
        if visible_from(ask.created_at, rank_of(d, eligible), bool(ask.red_gate)) <= now:
            out.append(d.id)
    return out


def is_visible(db: Session, ask: models.Ask, device: models.Device, now: datetime, link_required: bool) -> bool:
    eligible = eligible_devices(db, now, link_required)
    if all(d.id != device.id for d in eligible):
        # The caller was just seen by the auth layer; treat it as eligible
        # even if its row has not been refreshed in this session.
        eligible.append(device)
    return visible_from(ask.created_at, rank_of(device, eligible), bool(ask.red_gate)) <= now


def escalation_delays(created_at: datetime, n_ranks: int, red_gate: bool, now: datetime) -> List[float]:
    """Seconds from `now` until each later rank's window opens (ranks 1..n-1)."""
    out = []
    for r in range(1, max(n_ranks, 1)):
        due = visible_from(created_at, r, red_gate)
        out.append(max(0.0, (due - now).total_seconds()))
    return out


# --- escalation timers ---------------------------------------------------

_loop: Optional[asyncio.AbstractEventLoop] = None
_tasks: set = set()


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called from the app's startup handler; without a loop no timer is
    armed and the poll path alone carries escalation."""
    global _loop
    _loop = loop


def fire_escalation(ask_id: str, session_factory, link_required_fn, publish_fn, now: Optional[datetime] = None) -> Optional[dict]:
    """Publish request.escalated for a still pending ask; None when nothing to do."""
    from .requests_api import _ask_to_collection_request  # late import: requests_api imports this module
    now = now or datetime.now(timezone.utc)
    with session_factory() as db:
        ask = db.get(models.Ask, ask_id)
        if ask is None or ask.state not in ("queued", "shown"):
            return None
        link_required = link_required_fn()
        ids = visible_to(db, ask, now, link_required)
        eligible = eligible_devices(db, now, link_required)
        rank0 = {d.id for d in eligible if rank_of(d, eligible) == 0}
        if not (set(ids) - rank0):
            return None  # no later rank's window has opened yet
        data = _ask_to_collection_request(ask) | {"request_id": ask.id, "visible_to": ids}
    return publish_fn("request.escalated", data)


def schedule_escalations(ask_id: str, delays: List[float], session_factory, link_required_fn, publish_fn) -> int:
    """Arm one timer per delay on the bound loop; returns how many were armed."""
    if _loop is None or not delays:
        return 0

    async def _one(delay: float):
        try:
            await asyncio.sleep(delay)
            await asyncio.to_thread(fire_escalation, ask_id, session_factory, link_required_fn, publish_fn)
        except Exception:
            pass

    def _arm():
        for d in delays:
            t = _loop.create_task(_one(d))
            _tasks.add(t)
            t.add_done_callback(_tasks.discard)

    _loop.call_soon_threadsafe(_arm)
    return len(delays)
