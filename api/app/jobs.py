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
  3. renew immediately before execution and while long operations run;
     fence ownership by lease owner and claim generation (attempts);
  4. run it and commit database effects with result/done_at in one
     transaction. Network IO releases its connection and fences the result
     when it returns; receivers deduplicate signed delivery IDs;
  5. on an exception, release only the same lease generation with backoff;
     after MAX_ATTEMPTS the row is closed with last_error.

run_due snapshots candidate IDs and claims one just before execution, so
slow jobs never consume another queued job's lease while it is waiting.

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
from dataclasses import dataclass
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
        # hold deliberately remains answerable after its deadline. Its one
        # original expiry job records "held"; reconciliation must not grow
        # an endless chain of additional completed jobs.
        models.Ask.expiry_policy != "hold",
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

def claim_due(db, owner: str, now: Optional[datetime] = None, limit: int = CLAIM_BATCH,
              job_ids: Optional[List[str]] = None) -> List[models.ScheduledJob]:
    """Lease up to `limit` due jobs for `owner`. Each lease is a
    compare-and-set on the row, so two workers never both get one."""
    now = _naive(now or datetime.now(timezone.utc))
    lease_until = now + timedelta(seconds=LEASE_SECONDS)
    due_filter = (
        models.ScheduledJob.done_at.is_(None),
        models.ScheduledJob.run_at <= now,
        (models.ScheduledJob.lease_expires_at.is_(None)) | (models.ScheduledJob.lease_expires_at <= now),
    )
    stmt = select(models.ScheduledJob.id, models.ScheduledJob.lease_expires_at).where(*due_filter) \
        .order_by(models.ScheduledJob.run_at.asc()).limit(limit)
    if job_ids is not None:
        stmt = stmt.where(models.ScheduledJob.id.in_(job_ids))
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

@dataclass(frozen=True)
class Lease:
    job_id: str
    owner: str
    generation: int  # attempts increments on every successful claim


class LeaseLost(RuntimeError):
    """The worker must not execute or overwrite a newer lease holder."""


def _lease_filter(lease: Lease):
    return (models.ScheduledJob.id == lease.job_id,
            models.ScheduledJob.lease_owner == lease.owner,
            models.ScheduledJob.attempts == lease.generation,
            models.ScheduledJob.done_at.is_(None))


def guard(db, lease: Optional[Lease], now: datetime) -> None:
    """Fence a business transaction and renew while taking its row lock.

    PostgreSQL claimants skip this locked row; SQLite takes a writer lock.
    Callers commit their effects and complete the job in this transaction.
    Network IO is performed outside it and fenced again before recording.
    """
    if lease is None:
        return
    now = _naive(now)
    changed = db.execute(update(models.ScheduledJob).where(
        *_lease_filter(lease), models.ScheduledJob.lease_expires_at > now,
    ).values(lease_expires_at=now + timedelta(seconds=LEASE_SECONDS))).rowcount
    if changed != 1:
        raise LeaseLost(f"job {lease.job_id} lease no longer belongs to this worker")


def complete(db, lease: Optional[Lease], result: Optional[dict], now: datetime) -> None:
    if lease is None:
        return
    changed = db.execute(update(models.ScheduledJob).where(*_lease_filter(lease)).values(
        done_at=_naive(now), result=result, lease_owner=None, lease_expires_at=None)).rowcount
    if changed != 1:
        raise LeaseLost(f"job {lease.job_id} was superseded before completion")


def _renew(session_factory, lease: Lease, now: datetime) -> bool:
    with session_factory() as db:
        try:
            guard(db, lease, now)
            db.commit()
            return True
        except LeaseLost:
            db.rollback()
            return False


def _run_one(job_id: str, kind: str, ask_id: str, session_factory, now: datetime,
             ref_id: Optional[str] = None, lease: Optional[Lease] = None) -> Optional[dict]:
    from . import routing, events
    from .auth import google_link_required
    from .expiry import expire_ask
    if kind == KIND_WEBHOOK:
        from .webhooks import attempt_delivery
        return attempt_delivery(ref_id or "", session_factory, now, lease=lease)
    with session_factory() as db:
        guard(db, lease, now)
        if kind == KIND_ESCALATE:
            from .requests_api import _ask_to_collection_request
            ask = db.execute(select(models.Ask).where(models.Ask.id == ask_id).with_for_update()).scalar_one_or_none()
            env = None
            if ask is not None and ask.state in ("queued", "shown"):
                ids = routing.visible_to(db, ask, now, google_link_required())
                eligible = routing.eligible_devices(db, now, google_link_required(), ask.tenant_id)
                rank0 = {d.id for d in eligible if routing.rank_of(d, eligible) == 0}
                if set(ids) - rank0:
                    env = events.enqueue(db, "request.escalated",
                                         _ask_to_collection_request(ask) | {"request_id": ask.id, "visible_to": ids},
                                         tenant_id=ask.tenant_id, device_ids=ids)
            result = {"action": "escalated", "event_id": env["id"]} if env else {"action": "nothing_to_do"}
        elif kind == KIND_EXPIRE:
            result = expire_ask(db, ask_id, now, commit=False) or {"action": "nothing_to_do"}
        else:
            raise ValueError(f"unknown job kind: {kind}")
        complete(db, lease, result, now)
        db.commit()
    events.wake_all()
    return result


def run_claimed(jobs: List[models.ScheduledJob], session_factory=None, now: Optional[datetime] = None) -> List[dict]:
    """Fence every result by owner and generation; renew long-running work.

    A claimed batch may have waited longer than a lease. Each job is
    renewed immediately before execution, or skipped if its lease expired.
    run_due() avoids that wait entirely by claiming one job near execution.
    """
    session_factory = session_factory or _session_factory()
    base = now or datetime.now(timezone.utc)
    started = time.monotonic()
    def clock():
        return base + timedelta(seconds=time.monotonic() - started)
    results: List[dict] = []
    for job in jobs:
        job_id, kind, ask_id, attempts, ref_id = job.id, job.kind, job.ask_id, int(job.attempts or 0), job.ref_id
        if not job.lease_owner:
            continue
        lease = Lease(job_id, job.lease_owner, attempts)
        if not _renew(session_factory, lease, clock()):
            continue
        stop = threading.Event()
        lost = threading.Event()
        def heartbeat(stop=stop, lost=lost, lease=lease, job_id=job_id):
            while not stop.wait(max(0.05, min(LEASE_SECONDS / 3.0, 10.0))):
                try:
                    if not _renew(session_factory, lease, clock()):
                        lost.set()
                        return
                except Exception:
                    # A database outage cannot extend ownership. The
                    # transactional guard will reject an expired lease.
                    log.warning("could not renew job %s", job_id)
        renewer = threading.Thread(target=heartbeat, name="hca-job-lease", daemon=True)
        renewer.start()
        try:
            result = _run_one(job_id, kind, ask_id, session_factory, clock(), ref_id, lease)
            # Real handlers complete with their effects. This fallback also
            # supports a custom handler, but can never overwrite a takeover.
            with session_factory() as db:
                row = db.get(models.ScheduledJob, job_id)
                if row is None or row.attempts != lease.generation:
                    raise LeaseLost(f"job {job_id} was reclaimed during execution")
                if row.done_at is None:
                    if lost.is_set():
                        raise LeaseLost(f"job {job_id} lease renewal failed")
                    guard(db, lease, clock())
                    complete(db, lease, result, clock())
                    db.commit()
            worker_state["done"] += 1
            results.append({"job_id": job_id, "kind": kind, "request_id": ask_id, **(result or {})})
        except LeaseLost:
            log.info("job %s lease changed; stale result discarded", job_id)
        except Exception as exc:
            worker_state["failed"] += 1
            worker_state["last_error"] = f"{kind} {ask_id}: {exc}"[:200]
            log.warning("job %s (%s %s) failed on attempt %d: %s", job_id, kind, ask_id, attempts, exc)
            with session_factory() as db:
                try:
                    guard(db, lease, clock())
                    if attempts >= MAX_ATTEMPTS:
                        values = dict(done_at=_naive(clock()), last_error=str(exc)[:500], lease_owner=None, lease_expires_at=None)
                    else:
                        backoff = timedelta(seconds=min(2 ** attempts, 60))
                        values = dict(last_error=str(exc)[:500], lease_owner=None, lease_expires_at=_naive(clock()) + backoff)
                    db.execute(update(models.ScheduledJob).where(*_lease_filter(lease)).values(**values))
                    db.commit()
                except LeaseLost:
                    db.rollback()
        finally:
            stop.set()
            renewer.join(timeout=5.0)
    return results


def run_due(owner: Optional[str] = None, session_factory=None, now: Optional[datetime] = None, limit: int = CLAIM_BATCH) -> List[dict]:
    """Snapshot due IDs, then claim each immediately before its execution.

    Newly scheduled retries wait for the next pass, and a slow first job
    never consumes the lease of later rows in the batch.
    """
    session_factory = session_factory or _session_factory()
    owner = owner or worker_state.get("owner") or _owner_id()
    base = now or datetime.now(timezone.utc)
    started = time.monotonic()
    def clock():
        return base + timedelta(seconds=time.monotonic() - started)
    with session_factory() as db:
        ids = db.execute(select(models.ScheduledJob.id).where(
            models.ScheduledJob.done_at.is_(None), models.ScheduledJob.run_at <= _naive(base),
            (models.ScheduledJob.lease_expires_at.is_(None)) | (models.ScheduledJob.lease_expires_at <= _naive(base)),
        ).order_by(models.ScheduledJob.run_at).limit(limit)).scalars().all()
    results: List[dict] = []
    for job_id in ids:
        with session_factory() as db:
            claimed = claim_due(db, owner, clock(), limit=1, job_ids=[job_id])
        if claimed:
            results.extend(run_claimed(claimed, session_factory, clock()))
    return results


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
