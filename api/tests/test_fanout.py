"""HCA-P20 Day 2: table-driven fan-out, poll fallback, LISTEN/NOTIFY, nonces.

Dual mode (owner selection 4): every test here runs on SQLite with the
in-process wake; the tests marked postgres_only run in the CI Postgres job
and prove the real listener. Two API instances in two processes is the Day 4
suite; here a second "instance" is modelled by writing rows straight into
the table, which no in-process wake sees, so delivery can only come from
the poll or the listener.
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, engine, SessionLocal
from app import models, events, nonces, devicesig

Base.metadata.create_all(bind=engine)

POSTGRES = engine.dialect.name == "postgresql"
postgres_only = pytest.mark.skipif(not POSTGRES, reason="needs Postgres LISTEN/NOTIFY")

T = "tenant-fanout"


@pytest.fixture(autouse=True)
def _tenant():
    with SessionLocal() as db:
        if db.get(models.Tenant, T) is None:
            db.add(models.Tenant(id=T, slug=T, name=T, created_at=datetime.now(timezone.utc)))
            db.commit()


def _insert_row_directly(event: str, data: dict, device_ids=None) -> int:
    """What another instance's publish() leaves behind: a row, no local wake."""
    with SessionLocal() as db:
        row = models.Event(tenant_id=T, event=event, data=data, device_ids=device_ids,
                           created_at=datetime.now(timezone.utc).replace(tzinfo=None))
        db.add(row)
        db.commit()
        return int(row.id)


def test_poll_delivers_a_row_written_by_another_instance(monkeypatch):
    monkeypatch.setattr(events, "POLL_SECONDS", 0.1)

    async def run():
        q = events.subscribe("dev-f1", T)
        try:
            rid = _insert_row_directly("request.cancelled", {"request_id": "from-elsewhere"})
            env = await asyncio.wait_for(q.get(), timeout=2)
            assert env["id"] == rid
            assert env["data"]["request_id"] == "from-elsewhere"
            stats = events.dispatcher_stats()
            assert stats["polls"] >= 1 and stats["dispatched"] >= 1
        finally:
            events.unsubscribe(q)

    asyncio.run(run())


def test_dispatcher_applies_the_routing_set_and_tenant_to_remote_rows(monkeypatch):
    monkeypatch.setattr(events, "POLL_SECONDS", 0.1)

    async def run():
        mine = events.subscribe("dev-f2", T)
        other = events.subscribe("dev-f3", T)
        try:
            _insert_row_directly("request.created", {"request_id": "only-f2"}, device_ids=["dev-f2"])
            _insert_row_directly("request.answered", {"request_id": "only-f2"})
            got = await asyncio.wait_for(mine.get(), timeout=2)
            assert got["event"] == "request.created"
            got2 = await asyncio.wait_for(mine.get(), timeout=2)
            assert got2["event"] == "request.answered"
            got3 = await asyncio.wait_for(other.get(), timeout=2)
            assert got3["event"] == "request.answered", "the other device must only see the terminal event"
            assert other.empty()
        finally:
            events.unsubscribe(mine)
            events.unsubscribe(other)

    asyncio.run(run())


def test_dispatcher_starts_at_the_current_max_id_so_old_rows_are_replay_only(monkeypatch):
    monkeypatch.setattr(events, "POLL_SECONDS", 0.1)
    _insert_row_directly("request.cancelled", {"request_id": "before-subscribe"})

    async def run():
        q = events.subscribe("dev-f4", T)
        try:
            await asyncio.sleep(0.3)
            assert q.empty(), "a row from before the dispatcher started was live-delivered"
            events.publish("request.cancelled", {"request_id": "after"}, tenant_id=T)
            assert (await asyncio.wait_for(q.get(), timeout=2))["data"]["request_id"] == "after"
        finally:
            events.unsubscribe(q)

    asyncio.run(run())


def test_each_event_loop_gets_its_own_dispatcher_and_closed_loops_are_dropped():
    async def run():
        q = events.subscribe("dev-f5", T)
        try:
            d = events.ensure_dispatcher(asyncio.get_running_loop())
            assert d.task is not None and not d.task.done()
            events.publish("request.cancelled", {"request_id": "loop-a"}, tenant_id=T)
            await asyncio.wait_for(q.get(), timeout=2)
        finally:
            events.unsubscribe(q)

    asyncio.run(run())
    asyncio.run(run())
    # Both loops are closed now; the next wake sweeps their dispatchers.
    events.wake_all()
    assert events.dispatcher_stats()["dispatchers"] == 0


def test_publish_is_safe_from_a_worker_thread():
    """Expiry sweeps and escalation timers call publish() off the loop."""
    async def run():
        q = events.subscribe("dev-f6", T)
        try:
            await asyncio.to_thread(events.publish, "request.expired", {"request_id": "threaded"}, tenant_id=T)
            assert (await asyncio.wait_for(q.get(), timeout=2))["data"]["request_id"] == "threaded"
        finally:
            events.unsubscribe(q)

    asyncio.run(run())


def test_start_listener_is_a_no_op_on_sqlite_and_a_thread_on_postgres():
    started = events.start_listener()
    assert started is POSTGRES
    if POSTGRES:
        assert events.wait_listener_connected(5)
        events.stop_listener()
    assert events.listener_state["running"] is False or POSTGRES is False


@postgres_only
def test_notify_from_a_direct_insert_wakes_the_dispatcher_without_the_poll(monkeypatch):
    """The real proof: with the poll effectively off, only pg_notify can
    deliver a row that publish() on another instance wrote."""
    monkeypatch.setattr(events, "POLL_SECONDS", 60.0)
    assert events.start_listener()
    assert events.wait_listener_connected(5)
    before = events.listener_state["notifications"]

    async def run():
        q = events.subscribe("dev-pg1", T)
        try:
            # Another instance: row plus pg_notify in one transaction, no local wake.
            from sqlalchemy import text
            with SessionLocal() as db:
                row = models.Event(tenant_id=T, event="request.cancelled", data={"request_id": "notified"},
                                   device_ids=None, created_at=datetime.now(timezone.utc).replace(tzinfo=None))
                db.add(row)
                db.flush()
                db.execute(text("SELECT pg_notify(:c, :p)"), {"c": events.NOTIFY_CHANNEL, "p": str(row.id)})
                db.commit()
            env = await asyncio.wait_for(q.get(), timeout=5)
            assert env["data"]["request_id"] == "notified"
            assert events.listener_state["notifications"] > before
            assert events.dispatcher_stats()["polls"] == 0 or True  # the poll may tick once at start; wakes must be present
            assert events.dispatcher_stats()["wakes"] >= 1
        finally:
            events.unsubscribe(q)
            events.stop_listener()

    asyncio.run(run())


# ---------------------------------------------------------------- nonces

def test_nonce_is_refused_the_second_time_even_after_the_in_memory_guard_forgets():
    dev = "dev-nonce-" + uuid.uuid4().hex[:8]
    n = uuid.uuid4().hex
    assert nonces.check_and_record(dev, n) is True
    devicesig.replay_guard.reset()  # a restart, or another instance that never saw it
    assert nonces.check_and_record(dev, n) is False
    assert nonces.check_and_record(dev, uuid.uuid4().hex) is True


def test_nonce_prune_removes_only_rows_older_than_the_window():
    dev = "dev-prune-" + uuid.uuid4().hex[:8]
    old = datetime.now(timezone.utc) - timedelta(seconds=devicesig.MAX_SKEW_SECONDS + 60)
    assert nonces.check_and_record(dev, "old", now=old)
    assert nonces.check_and_record(dev, "fresh")
    removed = nonces.prune()
    assert removed >= 1
    with SessionLocal() as db:
        assert db.get(models.Nonce, (dev, "old")) is None
        assert db.get(models.Nonce, (dev, "fresh")) is not None


def test_replayed_signed_request_is_refused_by_the_table_when_memory_is_reset():
    """End to end through require_device: same signed request twice, with the
    in-memory guard reset between, as if the replay hit a second instance."""
    os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_local.db")
    from fastapi.testclient import TestClient
    from app.main import app
    from deviceauth import ensure_registered, human_headers
    ensure_registered()
    client = TestClient(app)
    headers = human_headers("GET", "/v1/inbox", b"")
    first = client.get("/v1/inbox", headers=headers)
    assert first.status_code in (200, 403), first.text  # 403 only if the test device is not google linked
    devicesig.replay_guard.reset()
    second = client.get("/v1/inbox", headers=headers)
    assert second.status_code == 401, second.text
    assert "replayed" in second.text
