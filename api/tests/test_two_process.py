"""HCA-P20 Day 4: two API processes on one database.

The Day 4 acceptance: two uvicorn processes (as two Cloud Run instances),
two SSE subscribers (one on each), two job workers (one in each). Asks are
created through both; every subscriber receives every event of its tenant
exactly once, wherever it was published; due jobs run exactly once across
both workers; a reconnect with Last-Event-ID replays what was missed; the
metrics page answers on both.

Dual mode (owner selection 4): on Postgres (CI) the cross-process wake is
LISTEN/NOTIFY and the test asserts both listeners are connected; on SQLite
the 2 s poll carries the fan-out and the same assertions hold. Processes
share the test's DATABASE_URL, admin token and pepper, so keys and devices
the test writes are the ones the servers verify.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_local.db")

from sqlalchemy import select, func

from app.db import Base, engine, SessionLocal, DATABASE_URL
from app import models
from authhelper import agent_key, ADMIN_AUTH, ADMIN_TOKEN
from deviceauth import human_headers, ensure_registered, DEVICE_ID
from overdue import backdate

Base.metadata.create_all(bind=engine)

IS_POSTGRES = DATABASE_URL.startswith("postgresql")
API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N_ASKS = 6
N_JOBS = 8


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, name: str):
        self.name = name
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": DATABASE_URL, "HCA_ADMIN_TOKEN": ADMIN_TOKEN,
            "HCA_API_KEY_PEPPER": os.environ["HCA_API_KEY_PEPPER"],
            "HCA_REQUIRE_GOOGLE_LINK": "0", "WEBHOOK_RETRY_DELAYS": "0,0",
            "HCA_JOBS_POLL_SECONDS": "0.5", "HCA_EVENT_POLL_SECONDS": "1",
            "PYTHONPATH": API_DIR,
        })
        self.log = open(os.path.join(API_DIR, f"two_process_{name}.log"), "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=API_DIR, env=env, stdout=self.log, stderr=subprocess.STDOUT)

    def wait_ready(self, timeout=30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} exited early; see two_process_{self.name}.log")
            try:
                r = httpx.get(self.base + "/health", timeout=2)
                if r.status_code == 200:
                    return r.json()
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"{self.name} not ready; see two_process_{self.name}.log")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


class StreamReader:
    """Reads an SSE stream in a thread and collects (id, event, data) frames."""

    def __init__(self, server: Server, since=None):
        self.server = server
        self.frames = []
        self.ids = []
        self._stop = threading.Event()
        self.error = None
        path = "/v1/inbox/stream" + (f"?since={since}" if since is not None else "")
        self.headers = human_headers("GET", path)
        self.path = path
        self.connected = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            with httpx.stream("GET", self.server.base + self.path, headers=self.headers, timeout=httpx.Timeout(5, read=60)) as r:
                assert r.status_code == 200, r.status_code
                cur = {}
                for line in r.iter_lines():
                    if self._stop.is_set():
                        break
                    if line.startswith(": connected"):
                        self.connected.set()
                    if line == "":
                        if "event" in cur:
                            self.frames.append(cur)
                            if "id" in cur:
                                self.ids.append(int(cur["id"]))
                        cur = {}
                        continue
                    if ":" in line and not line.startswith(":"):
                        k, v = line.split(":", 1)
                        cur[k] = v.strip()
        except Exception as exc:  # reported by the test, not swallowed
            self.error = exc

    def events_named(self, name):
        return [f for f in self.frames if f["event"] == name]

    def stop(self):
        self._stop.set()


def _wait(predicate, timeout=20.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def _post_ask(server: Server, key: str, title: str, deadline_minutes=60):
    body = {"caller": "two-proc-agent", "audience": "owner", "title": title, "control": "radio",
            "options_or_fields": [{"id": "a", "label": "A"}], "suggested": {"option_id": "a", "why": "w"},
            "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
            "deadline_at": (datetime.now(timezone.utc) + timedelta(minutes=deadline_minutes)).isoformat()}
    r = httpx.post(server.base + "/v1/requests", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()["request_id"]


@pytest.fixture(scope="module")
def servers():
    ensure_registered()
    a, b = Server("a"), Server("b")
    try:
        ha, hb = a.wait_ready(), b.wait_ready()
        yield a, b, ha, hb
    finally:
        a.stop()
        b.stop()


def test_two_processes_two_subscribers_two_workers(servers):
    a, b, health_a, health_b = servers
    key = agent_key("two-proc-agent")
    # keep the shared test device current and first in rank
    with SessionLocal() as db:
        d = db.get(models.Device, DEVICE_ID)
        d.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
        d.revoked_at = None
        db.commit()

    # 1. both processes are up, each with its own jobs worker; on Postgres each listener is on LISTEN
    for h in (health_a, health_b):
        assert h["jobs"]["worker"] == "running"
        if IS_POSTGRES:
            assert h["events"]["listener"] == "connected", h

    # 2. one subscriber per process
    sa, sb = StreamReader(a), StreamReader(b)
    assert sa.connected.wait(10) and sb.connected.wait(10), (sa.error, sb.error)

    # 3. asks created through both processes alternately
    ids = [_post_ask(a if i % 2 == 0 else b, key, f"two process ask {i}") for i in range(N_ASKS)]

    def both_saw_created():
        return (len([f for f in sa.events_named("request.created") if json.loads(f["data"])["data"]["request_id"] in ids]) >= N_ASKS
                and len([f for f in sb.events_named("request.created") if json.loads(f["data"])["data"]["request_id"] in ids]) >= N_ASKS)
    assert _wait(both_saw_created, 20), (len(sa.frames), len(sb.frames), sa.error, sb.error)

    for s in (sa, sb):
        created = [json.loads(f["data"])["data"]["request_id"] for f in s.events_named("request.created")]
        mine = Counter(r for r in created if r in ids)
        assert set(mine) == set(ids), f"{s.server.name} missed asks"
        assert all(c == 1 for c in mine.values()), f"{s.server.name} duplicated an event: {mine}"
        assert len(s.ids) == len(set(s.ids)), f"{s.server.name} repeated an SSE id"

    # 4. due jobs race the two workers: every expiry exactly once
    job_ids = [_post_ask(a if i % 2 == 0 else b, key, f"two process job {i}") for i in range(N_JOBS)]
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).replace(tzinfo=None)
    with SessionLocal() as db:
        for rid in job_ids:
            backdate(rid)
        for j in db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id.in_(job_ids))).scalars():
            if j.kind == "expire":
                j.run_at = past
        db.commit()

    def all_expired():
        with SessionLocal() as db:
            return all(db.get(models.Ask, rid).state == "expired" for rid in job_ids)
    assert _wait(all_expired, 20), "workers did not expire every due ask"

    with SessionLocal() as db:
        jobs_rows = db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id.in_(job_ids),
                                                                  models.ScheduledJob.kind == "expire")).scalars().all()
        assert len(jobs_rows) == N_JOBS and all(j.done_at is not None and j.attempts == 1 for j in jobs_rows), \
            [(j.ask_id, j.attempts, j.done_at) for j in jobs_rows]
        audits = db.execute(select(func.count(models.AuditRow.id)).where(models.AuditRow.ask_id.in_(job_ids))).scalar()
        assert audits == N_JOBS, "an expiry wrote more than one deny record"

    ma = httpx.get(a.base + "/v1/internal/metrics", headers=ADMIN_AUTH, timeout=10).json()
    mb = httpx.get(b.base + "/v1/internal/metrics", headers=ADMIN_AUTH, timeout=10).json()
    assert ma["jobs"]["done"] + mb["jobs"]["done"] >= N_JOBS
    assert ma["jobs"]["failed"] == 0 and mb["jobs"]["failed"] == 0
    assert ma["instance"]["pid"] != mb["instance"]["pid"]

    # 5. both subscribers receive each request.expired exactly once
    def both_saw_expired():
        return all(len([f for f in s.events_named("request.expired")
                        if json.loads(f["data"])["data"].get("request_id") in job_ids]) >= N_JOBS for s in (sa, sb))
    assert _wait(both_saw_expired, 20), (len(sa.events_named("request.expired")), len(sb.events_named("request.expired")))
    for s in (sa, sb):
        expired = Counter(json.loads(f["data"])["data"]["request_id"] for f in s.events_named("request.expired"))
        assert all(expired[r] == 1 for r in job_ids), f"{s.server.name}: {expired}"

    # 6. reconnect with Last-Event-ID on the other process replays what was missed
    last_b = max(sb.ids)
    sb.stop()
    missed = [_post_ask(a, key, f"while b was away {i}") for i in range(3)]
    sb2 = StreamReader(b, since=last_b)
    assert sb2.connected.wait(10), sb2.error
    assert _wait(lambda: len([f for f in sb2.events_named("request.created")
                              if json.loads(f["data"])["data"]["request_id"] in missed]) >= 3, 20), sb2.frames[:3]
    replayed = [json.loads(f["data"])["data"]["request_id"] for f in sb2.events_named("request.created")]
    assert Counter(r for r in replayed if r in missed) == Counter(missed)
    assert all(i > last_b for i in sb2.ids)

    mb = httpx.get(b.base + "/v1/internal/metrics", headers=ADMIN_AUTH, timeout=10).json()
    assert mb["replay"]["replays"] >= 1 and mb["replay"]["gaps"] == 0
    assert mb["drops"]["total"] == 0 and ma["drops"]["total"] == 0
    assert mb["streams"]["open"] >= 1 and mb["streams"]["lag_events_max"] >= 0
    assert mb["events"]["newest_id"] >= max(sb2.ids)

    sa.stop()
    sb2.stop()


def test_metrics_route_needs_the_admin_bearer(servers):
    a, _, _, _ = servers
    assert httpx.get(a.base + "/v1/internal/metrics", timeout=10).status_code == 401
    assert httpx.get(a.base + "/v1/internal/metrics", headers={"Authorization": "Bearer wrong"}, timeout=10).status_code == 403
    page = httpx.get(a.base + "/v1/internal/metrics", headers=ADMIN_AUTH, timeout=10)
    assert page.status_code == 200
    assert set(page.json()) >= {"events", "streams", "drops", "replay", "listener", "jobs", "db_pool", "instance"}
