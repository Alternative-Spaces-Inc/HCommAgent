"""HCA-P20 Day 3: leased scheduled_jobs for escalation and expiry.

Dual mode (owner selection 4): everything here runs on SQLite, where the
claim is the compare-and-set on lease_expires_at; on Postgres in CI the same
code adds FOR UPDATE SKIP LOCKED. The concurrency proof races real threads
over the same due rows and counts executions.
"""

import os
import sys
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_local.db")

from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app.main import app
from app.db import SessionLocal
from app import models, jobs
from authhelper import agent_auth, ADMIN_AUTH
from deviceauth import ensure_registered, DEVICE_ID
from overdue import backdate

client = TestClient(app)


def _post_ask(title="job ask", deadline_minutes=60, expiry_policy=None):
    body = {"caller": "jobs-agent", "audience": "owner", "title": title, "control": "radio",
            "options_or_fields": [{"id": "a", "label": "A"}], "suggested": {"option_id": "a", "why": "w"},
            "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
            "deadline_at": (datetime.now(timezone.utc) + timedelta(minutes=deadline_minutes)).isoformat()}
    if expiry_policy:
        body["expiry_policy"] = expiry_policy
    r = client.post("/v1/requests", json=body, headers=agent_auth("jobs-agent"))
    assert r.status_code == 201, r.text
    return r.json()["request_id"]


def _jobs_for(ask_id):
    with SessionLocal() as db:
        return db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id == ask_id)
                          .order_by(models.ScheduledJob.run_at)).scalars().all()


def _make_due(ask_id, kind=None):
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).replace(tzinfo=None)
    with SessionLocal() as db:
        for j in db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id == ask_id)).scalars():
            if kind is None or j.kind == kind:
                j.run_at = past
        db.commit()


# ------------------------------------------------------------ scheduling

def test_creating_an_ask_writes_its_expire_job_in_the_same_transaction():
    rid = _post_ask()
    rows = _jobs_for(rid)
    kinds = Counter(j.kind for j in rows)
    assert kinds[jobs.KIND_EXPIRE] == 1
    with SessionLocal() as db:
        ask = db.get(models.Ask, rid)
        exp = [j for j in rows if j.kind == jobs.KIND_EXPIRE][0]
        assert exp.run_at == ask.expires_at
        assert exp.done_at is None and exp.lease_owner is None and exp.tenant_id == ask.tenant_id


def test_a_second_rank_gets_an_escalate_job_at_its_window_open():
    ensure_registered()
    client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 10}, headers=ADMIN_AUTH)
    # A second linked device at a later rank, seen recently.
    with SessionLocal() as db:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        second = db.get(models.Device, "dev_jobs_second")
        if second is None:
            me = db.get(models.Device, DEVICE_ID)
            second = models.Device(id="dev_jobs_second", label="second", public_key_pem=me.public_key_pem,
                                   tenant_id=me.tenant_id, created_at=now, priority=200, google_sub=me.google_sub)
            db.add(second)
        second.priority = 200
        second.revoked_at = None
        second.last_seen_at = now
        second.google_sub = db.get(models.Device, DEVICE_ID).google_sub or "sub-second"
        db.commit()
    with SessionLocal() as db:
        me = db.get(models.Device, DEVICE_ID)
        me.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    try:
        rid = _post_ask("ranked ask")
    finally:
        # leave the shared device as other test modules expect it
        client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 100}, headers=ADMIN_AUTH)
        with SessionLocal() as db:
            second = db.get(models.Device, "dev_jobs_second")
            second.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
    rows = _jobs_for(rid)
    esc = [j for j in rows if j.kind == jobs.KIND_ESCALATE]
    # one escalate row per later rank (other tests may leave more devices in
    # the shared database, so at least one); the first opens at 90 s (P14
    # fallback window), the next one window later
    assert esc, [(j.kind, j.run_at) for j in rows]
    with SessionLocal() as db:
        ask = db.get(models.Ask, rid)
    assert abs((esc[0].run_at - ask.created_at).total_seconds() - 90) < 1
    for r, j in enumerate(esc, start=1):
        assert abs((j.run_at - ask.created_at).total_seconds() - 90 * r) < 1


# --------------------------------------------------------------- running

def test_due_expire_job_runs_once_and_expires_the_ask():
    rid = _post_ask()
    backdate(rid)
    _make_due(rid, jobs.KIND_EXPIRE)
    ran = jobs.run_due(owner="t1")
    mine = [r for r in ran if r["request_id"] == rid]
    assert len(mine) == 1 and mine[0]["action"] == "auto_denied"
    with SessionLocal() as db:
        assert db.get(models.Ask, rid).state == "expired"
    job = [j for j in _jobs_for(rid) if j.kind == jobs.KIND_EXPIRE][0]
    assert job.done_at is not None and job.result["action"] == "auto_denied" and job.attempts == 1
    assert jobs.run_due(owner="t2") == [] or all(r["request_id"] != rid for r in jobs.run_due(owner="t2"))


def test_an_answered_ask_makes_its_jobs_a_no_op():
    rid = _post_ask()
    with SessionLocal() as db:
        ask = db.get(models.Ask, rid)
        ask.state = "answered"
        db.commit()
    backdate(rid)
    _make_due(rid)
    ran = [r for r in jobs.run_due(owner="t3") if r["request_id"] == rid]
    assert ran and all(r["action"] == "nothing_to_do" for r in ran)
    with SessionLocal() as db:
        assert db.get(models.Ask, rid).state == "answered"


def test_concurrent_workers_run_each_due_job_exactly_once(monkeypatch):
    """The Day 3 acceptance: several workers racing over the same due rows;
    every job executes once in total, none twice, none never."""
    ids = [_post_ask(f"race {i}") for i in range(12)]
    for rid in ids:
        backdate(rid)
        _make_due(rid, jobs.KIND_EXPIRE)
    executed = Counter()
    lock = threading.Lock()
    real = jobs._run_one

    def counting(job_id, kind, ask_id, session_factory, now, *rest):
        with lock:
            executed[job_id] += 1
        return real(job_id, kind, ask_id, session_factory, now, *rest)

    monkeypatch.setattr(jobs, "_run_one", counting)
    errors = []

    def worker(name):
        try:
            for _ in range(6):
                jobs.run_due(owner=name, limit=3)
        except Exception as exc:  # surfaced below, so a locked SQLite file is a failure not a hang
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors
    expire_jobs = [j for rid in ids for j in _jobs_for(rid) if j.kind == jobs.KIND_EXPIRE]
    assert len(expire_jobs) == 12
    assert all(j.done_at is not None for j in expire_jobs), "a job was never run"
    assert all(executed[j.id] == 1 for j in expire_jobs), {j.id: executed[j.id] for j in expire_jobs if executed[j.id] != 1}
    with SessionLocal() as db:
        states = [db.get(models.Ask, rid).state for rid in ids]
        assert states == ["expired"] * 12
        audits = db.execute(select(func.count(models.AuditRow.id)).where(models.AuditRow.ask_id.in_(ids))).scalar()
        assert audits == 12, "an expiry wrote more than one deny record"


def test_a_failing_job_is_released_with_backoff_then_closed_after_max_attempts(monkeypatch):
    rid = _post_ask()
    backdate(rid)
    _make_due(rid, jobs.KIND_EXPIRE)
    monkeypatch.setattr(jobs, "_run_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(jobs, "MAX_ATTEMPTS", 2)
    now = datetime.now(timezone.utc)
    jobs.run_due(owner="f1", now=now)
    job = [j for j in _jobs_for(rid) if j.kind == jobs.KIND_EXPIRE][0]
    assert job.done_at is None and job.attempts == 1 and "boom" in job.last_error
    assert job.lease_owner is None and job.lease_expires_at > now.replace(tzinfo=None), "released with a backoff lease"
    # not claimable until the backoff lease passes
    assert all(r["request_id"] != rid for r in jobs.run_due(owner="f2", now=now))
    later = now + timedelta(seconds=5)
    jobs.run_due(owner="f3", now=later)
    job = [j for j in _jobs_for(rid) if j.kind == jobs.KIND_EXPIRE][0]
    assert job.done_at is not None and job.attempts == 2 and "boom" in job.last_error, "closed after MAX_ATTEMPTS"


def test_an_expired_lease_is_reclaimable_as_if_the_holder_died():
    rid = _post_ask()
    backdate(rid)
    _make_due(rid, jobs.KIND_EXPIRE)
    with SessionLocal() as db:
        j = [x for x in db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id == rid)).scalars()
             if x.kind == jobs.KIND_EXPIRE][0]
        j.lease_owner = "dead-instance"
        j.lease_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).replace(tzinfo=None)
        j.attempts = 1
        db.commit()
    ran = [r for r in jobs.run_due(owner="alive") if r["request_id"] == rid]
    assert ran and ran[0]["action"] == "auto_denied"
    job = [x for x in _jobs_for(rid) if x.kind == jobs.KIND_EXPIRE][0]
    assert job.done_at is not None and job.attempts == 2


# ------------------------------------------------------------- reconcile

def test_reconcile_gives_a_legacy_overdue_ask_an_expire_job_due_now():
    rid = _post_ask()
    with SessionLocal() as db:
        for j in db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id == rid)).scalars():
            db.delete(j)  # as if created before the table existed
        db.commit()
    backdate(rid)
    with SessionLocal() as db:
        assert jobs.reconcile(db) >= 1
        assert jobs.reconcile(db) == 0, "reconcile must not duplicate an open due job"
    rows = _jobs_for(rid)
    assert len(rows) == 1 and rows[0].kind == jobs.KIND_EXPIRE and rows[0].run_at <= datetime.now(timezone.utc).replace(tzinfo=None)


def test_reconcile_pulls_a_future_expire_job_forward_when_expires_at_moved():
    rid = _post_ask()  # expire job at +60 min
    backdate(rid)      # expires_at now in the past; the job still says +60 min
    with SessionLocal() as db:
        assert jobs.reconcile(db) == 1
    rows = [j for j in _jobs_for(rid) if j.kind == jobs.KIND_EXPIRE]
    assert len(rows) == 1, "moved, not duplicated"
    assert rows[0].run_at <= datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- worker

def test_worker_thread_runs_due_jobs_without_any_request(monkeypatch):
    monkeypatch.setattr(jobs, "POLL_SECONDS", 0.1)
    rid = _post_ask()
    backdate(rid)
    _make_due(rid, jobs.KIND_EXPIRE)
    jobs.stop_worker()
    owner = jobs.start_worker()
    try:
        assert owner and jobs.worker_state["running"]
        ok = jobs.wait_until(lambda: [j for j in _jobs_for(rid) if j.kind == jobs.KIND_EXPIRE][0].done_at is not None, 5)
        assert ok, "worker did not pick up the due job"
        with SessionLocal() as db:
            assert db.get(models.Ask, rid).state == "expired"
    finally:
        jobs.stop_worker()
    assert jobs.worker_state["running"] is False


def test_sweep_route_reports_the_leased_run_in_the_p19_shape():
    rid = _post_ask()
    backdate(rid)
    os.environ["SWEEP_TRIGGER_TOKEN"] = "test-sweep-token"
    r = client.post("/v1/internal/expiry-sweep", headers={"Authorization": "Bearer test-sweep-token"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(x["request_id"] == rid and x["action"] == "auto_denied" for x in body["results"])
    assert body["swept"] >= 1 and body["jobs_run"] >= 1 and "reconciled" in body


def test_health_reports_the_jobs_worker():
    body = client.get("/health").json()
    assert body["jobs"]["worker"] in ("running", "stopped")
    assert set(body["jobs"]) >= {"polls", "claimed", "done", "failed"}
