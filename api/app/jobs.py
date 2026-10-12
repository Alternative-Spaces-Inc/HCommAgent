"""Leased database jobs for escalation and expiry (HCA-P20 Day 3).

Before P20 the two timed actions on an ask ran in process: escalation of a
later rank as an asyncio timer on the instance that created the ask (P14),
expiry as an unleased scan hit by Cloud Scheduler (P19). A restart lost the
timers and a second instance would have run the scan twice.

Now every timed action is a scheduled_jobs row written in the same
transaction as the ask (app/requests_api.py): one `escalate` row per later
rank at the instant its window opens, one `expire` row at expires_at. A
worker thread in every instance polls for due rows and claims them:

  1. SELECT due rows (run_at <= now, not done, lease absent or expired),
     FOR UPDATE SKIP LOCKED on Postgres so competing claimants do not
     queue on each other's rows;
  2. per row a compare-and-set UPDATE on lease_expires_at: the row is ours
     only when exactly one row changed. This is what makes a job run once
     on every database, SQLite included, however many workers race;
  3. run it (escalation: app/routing.fire_escalation; expiry:
     app/expiry.expire_ask, both idempotent against the ask's state),
     record the result and done_at;
  4. on an exception, release the lease with backoff and count the attempt;
     after MAX_ATTEMPTS the row is closed with last_error.

reconcile() is the safety net for rows that predate this table and for an
expires_at moved by hand: every overdue non-terminal ask gets an expire job
due now when it has none. The sweep route (app/sweep_api.py) runs
reconcile() and one claim-and-run pass, so Cloud Scheduler keeps working as
a second trigger; the worker thread is the first.
"""

import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from . import models

log = logging.getLogger("hca.jobs")

KIND_ESCALATE = "escalate"
KIND_EXPIRE = "expire"
KIND_WEBHOOK = "webhook"  # HCA-P21a Day 2: one outbox attempt, ref_id = webhook_deliveries.id
KINDS = (KIND_ESCALATE, KIND_EXPIRE, KIND_WEBHOOK)

POLL_SECONDS = float(os.environ.get("HCA_JOBS_POLL_SECONDS", "2"))
LEASE_SECONDS = int(os.environ.get("HCA_JOBS_LEASE_SECONDS", "60"))
MAX_ATTEMPTS = int(os.environ.get("HCA_JOBS_MAX_ATTEMPTS", "5"))
CLAIM_BATCH = 20
RECONCILE_EVERY = 30  # worker polls between reconcile passes (60 s at the 2 s poll)

worker_state: Dict[str, Any] = {"running": False, "owner": None, "polls": 0, "claimed": 0, "done": 0, "failed": 0,
                                "reconciled": 0, "last_error": None}
_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _session_factory():
    from .db import SessionLocal
    return SessionLocal


# ------------------------------------------------------------------ writing

def schedule(db, *, tenant_id: str, ask_id: str, kind: str, run_at: datetime, now: Optional[datetime] = None,
             ref_id: Optional[str] = None) -> models.ScheduledJob:
    """Add one job row to the caller's transaction (the caller commits with the ask)."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind: {kind}")
    if kind == KIND_WEBHOOK and not ref_id:
        raise ValueError("webhook jobs need ref_id (the delivery id)")
    now = now or datetime.now(timezone.utc)
    row = models.ScheduledJob(tenant_id=tenant_id, ask_id=ask_id, kind=kind, run_at=_naive(run_at), created_at=_naive(now),
                              ref_id=ref_id)
    db.add(row)
    return row


def schedule_for_ask(db, ask: models.Ask, escalation_due: List[datetime], now: Optional[datetime] = None) -> int:
    """The rows an ask needs at creation: one escalate per later rank window
    and one expire at expires_at (none when the ask never expires)."""
    n = 0
    for due in escalation_due:
        schedule(db, tenant_id=ask.tenant_id, ask_id=ask.id, kind=KIND_ESCALATE, run_at=due, now=now)
        n += 1
    if ask.expires_at is not None:
        schedule(db, tenant_id=ask.tenant_id, ask_id=ask.id, kind=KIND_EXPIRE, run_at=ask.expires_at, now=now)
        n += 1
    return n


def reconcile(db, now: Optional[datetime] = None) -> int:
    """Give every overdue non-terminal ask an expire job due now when it has
    no open one (rows from before this table, or an expires_at moved by
    hand). Returns how many jobs were added. Commits."""
    from .requests_api import TERMINAL_STATES
    now = _naive(now or datetime.now(timezone.utc))
    overdue = db.execute(select(models.Ask.id, models.Ask.tenant_id).where(
        models.Ask.state.notin_(TERMINAL_STATES),
        models.Ask.expires_at.is_not(None),
        models.Ask.expires_at <= now,
    )).all()
    if not overdue:
        return 0
    ids = [a for a, _ in overdue]
    open_jobs = {j for (j,) in db.execute(select(models.ScheduledJob.ask_id).where(
        models.ScheduledJob.kind == KIND_EXPIRE,
        models.ScheduledJob.done_at.is_(None),
        models.ScheduledJob.ask_id.in_(ids),
        models.ScheduledJob.run_at <= now,
    )).all()}
    added = 0
    for ask_id, tenant_id in overdue:
        if ask_id in open_jobs:
            continue
        # A future-dated open expire job for this ask (expires_at was moved
        # earlier) is pulled forward instead of duplicated.
        moved = db.execute(update(models.ScheduledJob).where(
            models.ScheduledJob.ask_id == ask_id, models.ScheduledJob.kind == KIND_EXPIRE,
            models.ScheduledJob.done_at.is_(None), models.ScheduledJob.run_at > now,
        ).values(run_at=now)).rowcount
        if not moved:
            db.add(models.ScheduledJob(tenant_id=tenant_id, ask_id=ask_id, kind=KIND_EXPIRE, run_at=now, created_at=now))
        added += 1
    db.commit()
    worker_state["reconciled"] += added
    return added


# ----------------------------------------------------------------- claiming

def claim_due(db, owner: str, now: Optional[datetime] = None, limit: int = CLAIM_BATCH) -> List[models.ScheduledJob]:
    """Lease up to `limit` due jobs for `owner`. Each lease is a
    compare-and-set on the row, so two workers never both get one."""
    now = _naive(now or datetime.now(timezone.utc))
    lease_until = now + timedelta(seconds=LEASE_SECONDS)
    due_filter = (
        models.ScheduledJob.done_at.is_(None),
        models.ScheduledJob.run_at <= now,
        (models.ScheduledJob.lease_expires_at.is_(None)) | (models.ScheduledJob.lease_expires_at < now),
    )
    stmt = select(models.ScheduledJob.id, models.ScheduledJob.lease_expires_at).where(*due_filter) \
        .order_by(models.ScheduledJob.run_at.asc()).limit(limit)
    if db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    candidates = db.execute(stmt).all()
    claimed: List[str] = []
    for job_id, seen_lease in candidates:
        cas = update(models.ScheduledJob).where(
            models.ScheduledJob.id == job_id,
            models.ScheduledJob.done_at.is_(None),
            (models.ScheduledJob.lease_expires_at.is_(None)) if seen_lease is None
            else (models.ScheduledJob.lease_expires_at == seen_lease),
        ).values(lease_owner=owner, lease_expires_at=lease_until, attempts=models.ScheduledJob.attempts + 1)
        if db.execute(cas).rowcount == 1:
            claimed.append(job_id)
    db.commit()
    if not claimed:
        return []
    rows = db.execute(select(models.ScheduledJob).where(models.ScheduledJob.id.in_(claimed))
                      .order_by(models.ScheduledJob.run_at.asc())).scalars().all()
    worker_state["claimed"] += len(rows)
    return rows


# ------------------------------------------------------------------ running

def _run_one(job_id: str, kind: str, ask_id: str, session_factory, now: datetime, ref_id: Optional[str] = None) -> Optional[dict]:
    from . import routing
    from .auth import google_link_required
    from .events import publish
    from .expiry import expire_ask
    if kind == KIND_WEBHOOK:
        from .webhooks import attempt_delivery
        return attempt_delivery(ref_id or "", session_factory, now)
    if kind == KIND_ESCALATE:
        env = routing.fire_escalation(ask_id, session_factory, google_link_required, publish, now=now)
        return {"action": "escalated", "event_id": env.get("id")} if env else {"action": "nothing_to_do"}
    if kind == KIND_EXPIRE:
        with session_factory() as db:
            r = expire_ask(db, ask_id, now)
        return r or {"action": "nothing_to_do"}
    raise ValueError(f"unknown job kind: {kind}")


def run_claimed(jobs: List[models.ScheduledJob], session_factory=None, now: Optional[datetime] = None) -> List[dict]:
    """Run jobs this worker holds the lease on; close or release each."""
    session_factory = session_factory or _session_factory()
    now = now or datetime.now(timezone.utc)
    now_naive = _naive(now)
    results: List[dict] = []
    for job in jobs:
        job_id, kind, ask_id, attempts, ref_id = job.id, job.kind, job.ask_id, int(job.attempts or 0), job.ref_id
        try:
            result = _run_one(job_id, kind, ask_id, session_factory, now, ref_id)
            with session_factory() as db:
                db.execute(update(models.ScheduledJob).where(models.ScheduledJob.id == job_id)
                           .values(done_at=now_naive, result=result, lease_owner=None, lease_expires_at=None))
                db.commit()
            worker_state["done"] += 1
            results.append({"job_id": job_id, "kind": kind, "request_id": ask_id, **(result or {})})
        except Exception as exc:  # release with backoff, or close after MAX_ATTEMPTS
            worker_state["failed"] += 1
            worker_state["last_error"] = f"{kind} {ask_id}: {exc}"[:200]
            log.warning("job %s (%s %s) failed on attempt %d: %s", job_id, kind, ask_id, attempts, exc)
            with session_factory() as db:
                if attempts >= MAX_ATTEMPTS:
                    db.execute(update(models.ScheduledJob).where(models.ScheduledJob.id == job_id)
                               .values(done_at=now_naive, last_error=str(exc)[:500], lease_owner=None, lease_expires_at=None))
                else:
                    backoff = timedelta(seconds=min(2 ** attempts, 60))
                    db.execute(update(models.ScheduledJob).where(models.ScheduledJob.id == job_id)
                               .values(last_error=str(exc)[:500], lease_owner=None, lease_expires_at=now_naive + backoff))
                db.commit()
    return results


def run_due(owner: Optional[str] = None, session_factory=None, now: Optional[datetime] = None, limit: int = CLAIM_BATCH) -> List[dict]:
    """One claim-and-run pass: what the worker does every poll and what the
    sweep route does per hit."""
    session_factory = session_factory or _session_factory()
    owner = owner or worker_state.get("owner") or _owner_id()
    now = now or datetime.now(timezone.utc)
    with session_factory() as db:
        jobs = claim_due(db, owner, now, limit)
    if not jobs:
        return []
    return run_claimed(jobs, session_factory, now)


# ------------------------------------------------------------------- worker

def _work_forever(stop: threading.Event, owner: str) -> None:
    polls = 0
    while not stop.is_set():
        try:
            if polls % RECONCILE_EVERY == 0:
                with _session_factory()() as db:
                    reconcile(db)
            run_due(owner)
        except Exception as exc:
            worker_state["last_error"] = str(exc)[:200]
            log.warning("jobs worker pass failed: %s", exc)
        polls += 1
        worker_state["polls"] = polls
        stop.wait(POLL_SECONDS)


def start_worker() -> str:
    """Start this instance's worker thread (idempotent). Returns the owner id."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return worker_state["owner"]
    _worker_stop.clear()
    owner = _owner_id()
    worker_state["owner"] = owner
    worker_state["running"] = True
    _worker_thread = threading.Thread(target=_work_forever, args=(_worker_stop, owner), name="hca-jobs-worker", daemon=True)
    _worker_thread.start()
    return owner


def stop_worker(timeout: float = 5.0) -> None:
    global _worker_thread
    _worker_stop.set()
    t = _worker_thread
    if t is not None:
        t.join(timeout)
    _worker_thread = None
    worker_state["running"] = False


def wait_until(predicate, timeout: float = 5.0, step: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())
