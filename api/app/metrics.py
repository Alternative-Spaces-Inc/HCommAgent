"""Operational metrics for durable delivery (HCA-P20 Day 4).

One JSON page per instance, read by GET /v1/internal/metrics under the
platform admin bearer (app/metrics_api.py). Every number is computed on
request from this process and the database; nothing is sampled or stored.

  events          newest events row id, how many rows exist, this instance's
                  dispatchers and their lag behind the newest row (rows the
                  dispatcher has not read yet; 0 in steady state, bounded by
                  the 2 s poll when the listener is down)
  streams         open SSE streams on this instance with the last id each has
                  written to its client, and the largest lag among them
                  (newest row id minus last written id; an upper bound, since
                  rows of other tenants or devices are not owed to a stream)
  drops           envelopes refused by a full per-stream queue, total and per
                  device (a slow client, never a lost row: the client replays
                  from its Last-Event-ID on reconnect)
  replay          reconnect replays served, rows replayed, and gaps: replays
                  whose missed rows could not all be served (aged out of the
                  window, or truncated at max_events)
  listener        the LISTEN/NOTIFY thread state (connected, notifications,
                  errors)
  jobs            this instance's worker counters plus the shared backlog: due
                  rows not yet done and the age of the oldest one (the lag of
                  escalation and expiry across all instances)
  db_pool         SQLAlchemy pool occupancy for this process, the number to
                  watch against the Cloud SQL connection slots when
                  _MAX_INSTANCES is raised
  instance        hostname, pid and the Cloud Run revision and instance when
                  present, so three instances can be told apart
"""

import os
import re
import socket
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import func, select

from . import events, jobs, models


def _naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _pool_status(engine) -> Dict[str, Any]:
    pool = engine.pool
    out: Dict[str, Any] = {"class": type(pool).__name__}
    for name in ("size", "checkedin", "checkedout", "overflow"):
        fn = getattr(pool, name, None)
        if callable(fn):
            try:
                out[name] = fn()
            except Exception:
                pass
    try:
        out["status"] = pool.status()
    except Exception:
        pass
    m = re.search(r"Current Overflow: (-?\d+)", out.get("status", ""))
    if m and "overflow" not in out:
        out["overflow"] = int(m.group(1))
    return out


def collect(db) -> Dict[str, Any]:
    now = _naive_now()
    newest_id, event_rows = db.execute(select(func.max(models.Event.id), func.count(models.Event.id))).one()
    newest_id = int(newest_id or 0)

    stats = events.dispatcher_stats()
    with events._lock:
        dispatchers = [{"cursor": d.cursor, "lag_events": max(0, newest_id - d.cursor), **d.stats}
                       for d in events._dispatchers.values()]

    positions = events.stream_positions()
    stream_lag = max((newest_id - p["last_sent_id"] for p in positions), default=0)

    due_filter = (models.ScheduledJob.done_at.is_(None), models.ScheduledJob.run_at <= now)
    backlog, oldest_due = db.execute(select(func.count(models.ScheduledJob.id), func.min(models.ScheduledJob.run_at))
                                     .where(*due_filter)).one()
    leased = db.execute(select(func.count(models.ScheduledJob.id)).where(
        *due_filter, models.ScheduledJob.lease_expires_at.is_not(None), models.ScheduledJob.lease_expires_at >= now)).scalar()
    pending_total = db.execute(select(func.count(models.ScheduledJob.id)).where(models.ScheduledJob.done_at.is_(None))).scalar()
    failed_closed = db.execute(select(func.count(models.ScheduledJob.id)).where(
        models.ScheduledJob.done_at.is_not(None), models.ScheduledJob.last_error.is_not(None),
        models.ScheduledJob.result.is_(None))).scalar()

    return {
        "at": now.replace(tzinfo=timezone.utc).isoformat(),
        "events": {"newest_id": newest_id, "rows": int(event_rows or 0),
                   "dispatchers": dispatchers, "dispatch_lag_events": max((d["lag_events"] for d in dispatchers), default=0),
                   "wakes": stats["wakes"], "polls": stats["polls"], "dispatched": stats["dispatched"]},
        "streams": {"open": len(positions), "positions": positions, "lag_events_max": stream_lag,
                    "max_per_device": events.STREAM_MAX_PER_DEVICE, "superseded": events.stream_state["superseded"]},
        "drops": {"total": sum(events.dropped.values()), "by_device": dict(events.dropped)},
        "replay": dict(events.replay_state),
        "listener": dict(events.listener_state),
        "jobs": {**dict(jobs.worker_state),
                 "due_backlog": int(backlog or 0), "due_leased": int(leased or 0),
                 "oldest_due_age_seconds": (now - oldest_due).total_seconds() if oldest_due is not None else 0.0,
                 "pending_total": int(pending_total or 0), "failed_closed": int(failed_closed or 0)},
        "db_pool": _pool_status(db.get_bind()),
        "instance": {"hostname": socket.gethostname(), "pid": os.getpid(),
                     "revision": os.environ.get("K_REVISION"), "instance_id": os.environ.get("CLOUD_RUN_INSTANCE_ID")},
    }
