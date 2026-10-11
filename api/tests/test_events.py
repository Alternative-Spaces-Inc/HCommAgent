import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from datetime import datetime, timedelta, timezone

from app.db import Base, engine, SessionLocal
from app import models
from app.events import subscribe, unsubscribe, publish, replay, format_sse, parse_last_event_id, dropped, QUEUE_MAXSIZE

Base.metadata.create_all(bind=engine)


def _tenant(tenant_id: str) -> None:
    """events.tenant_id is a foreign key; Postgres enforces it where SQLite
    does not, so every made-up tenant these tests publish for gets a row."""
    with SessionLocal() as db:
        if db.get(models.Tenant, tenant_id) is None:
            db.add(models.Tenant(id=tenant_id, slug=tenant_id, name=tenant_id, created_at=datetime.now(timezone.utc)))
            db.commit()


for _t in ("tenant-a", "tenant-b", "tenant-d", "tenant-m", "tenant-r", "tenant-s", "tenant-w"):
    _tenant(_t)


def _clear(tenant_id):
    """The suite shares one SQLite file across runs; start each replay test
    from an empty log for its tenant."""
    with SessionLocal() as db:
        from sqlalchemy import delete
        db.execute(delete(models.Event).where(models.Event.tenant_id == tenant_id))
        db.commit()


def _events(tenant_id):
    with SessionLocal() as db:
        from sqlalchemy import select
        return db.execute(select(models.Event).where(models.Event.tenant_id == tenant_id)
                          .order_by(models.Event.id)).scalars().all()


def test_publish_reaches_subscriber_queue():
    async def run():
        q = subscribe("dev-a", "tenant-a")
        try:
            publish("request.created", {"request_id": "abc123"}, tenant_id="tenant-a", device_ids=["dev-a"])
            envelope = await asyncio.wait_for(q.get(), timeout=1)
            assert envelope["event"] == "request.created"
            assert envelope["data"]["request_id"] == "abc123"
            sse_text = format_sse(envelope)
            assert sse_text.startswith(f"id: {envelope['id']}\nevent: request.created\n")
            assert "abc123" in sse_text
        finally:
            unsubscribe(q)

    asyncio.run(run())


def test_publish_rejects_unknown_event():
    try:
        publish("request.made_up", {}, tenant_id="tenant-a")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_publish_filters_by_tenant_and_by_device_before_enqueue():
    """HCA-P19: authorization happens on the server, before the envelope
    reaches a queue; visible_to in the payload is not what keeps data in."""
    async def run():
        a1 = subscribe("dev-a1", "tenant-a")
        a2 = subscribe("dev-a2", "tenant-a")
        b1 = subscribe("dev-b1", "tenant-b")
        try:
            publish("request.created", {"request_id": "r1", "title": "secret for a1"}, tenant_id="tenant-a", device_ids=["dev-a1"])
            assert (await asyncio.wait_for(a1.get(), timeout=1))["data"]["request_id"] == "r1"
            assert a2.empty(), "a same-tenant device outside the routing set got the ask"
            assert b1.empty(), "another tenant's device got the ask"
            # terminal events go to the whole tenant, still never across it
            publish("request.answered", {"request_id": "r1"}, tenant_id="tenant-a")
            assert (await asyncio.wait_for(a1.get(), timeout=1))["event"] == "request.answered"
            assert (await asyncio.wait_for(a2.get(), timeout=1))["event"] == "request.answered"
            assert b1.empty()
        finally:
            for q in (a1, a2, b1):
                unsubscribe(q)

    asyncio.run(run())


def test_subscribe_requires_identity_and_publish_requires_tenant():
    for args in (("", "t"), ("d", "")):
        try:
            subscribe(*args)
            assert False, "expected ValueError"
        except ValueError:
            pass
    try:
        publish("request.created", {}, tenant_id="")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_queue_overflow_is_counted_not_silent():
    async def run():
        q = subscribe("dev-full", "tenant-a")
        try:
            before = dropped.get("dev-full", 0)
            for _ in range(QUEUE_MAXSIZE + 3):
                publish("request.answered", {"request_id": "x"}, tenant_id="tenant-a")
            assert dropped["dev-full"] == before + 3
        finally:
            unsubscribe(q)

    asyncio.run(run())


# HCA-P20 Day 1: durable rows and replay.

def test_publish_writes_a_row_before_delivery_and_the_envelope_carries_its_id():
    async def run():
        q = subscribe("dev-d1", "tenant-d")
        try:
            env = publish("request.created", {"request_id": "d-1"}, tenant_id="tenant-d", device_ids=["dev-d1"])
            assert isinstance(env["id"], int)
            rows = _events("tenant-d")
            assert [r.id for r in rows][-1] == env["id"]
            assert rows[-1].event == "request.created"
            assert rows[-1].data == {"request_id": "d-1"}
            assert rows[-1].device_ids == ["dev-d1"]
            got = await asyncio.wait_for(q.get(), timeout=1)
            assert got["id"] == env["id"]
            assert format_sse(got).startswith(f"id: {env['id']}\nevent: request.created\n")
        finally:
            unsubscribe(q)

    asyncio.run(run())


def test_event_ids_are_monotonic_across_publishes():
    ids = [publish("request.answered", {"request_id": f"m-{i}"}, tenant_id="tenant-m")["id"] for i in range(3)]
    assert ids == sorted(ids) and len(set(ids)) == 3


def test_replay_returns_missed_rows_for_the_device_only_in_id_order():
    _clear("tenant-r")
    _clear("tenant-s")
    first = publish("request.created", {"request_id": "r-0"}, tenant_id="tenant-r", device_ids=["dev-r1"])["id"]
    publish("request.created", {"request_id": "r-1"}, tenant_id="tenant-r", device_ids=["dev-r1"])
    publish("request.created", {"request_id": "r-2"}, tenant_id="tenant-r", device_ids=["dev-r2"])  # not for r1
    publish("request.answered", {"request_id": "r-1"}, tenant_id="tenant-r")                    # whole tenant
    publish("request.created", {"request_id": "s-1"}, tenant_id="tenant-s", device_ids=["dev-r1"])  # other tenant
    with SessionLocal() as db:
        got = replay(db, tenant_id="tenant-r", device_id="dev-r1", after_id=first)
    assert [e["data"]["request_id"] for e in got] == ["r-1", "r-1"]
    assert [e["event"] for e in got] == ["request.created", "request.answered"]
    assert got[0]["id"] < got[1]["id"]
    assert all(e["id"] > first for e in got)
    assert all("timestamp" in e for e in got)


def test_replay_honours_the_window_and_the_row_cap():
    _clear("tenant-w")
    env = publish("request.cancelled", {"request_id": "w-old"}, tenant_id="tenant-w")
    with SessionLocal() as db:
        row = db.get(models.Event, env["id"])
        row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)
        db.commit()
    for i in range(5):
        publish("request.cancelled", {"request_id": f"w-{i}"}, tenant_id="tenant-w")
    with SessionLocal() as db:
        got = replay(db, tenant_id="tenant-w", device_id="dev-w", after_id=0)
        assert "w-old" not in [e["data"]["request_id"] for e in got], "a row older than the window was replayed"
        assert [e["data"]["request_id"] for e in got] == [f"w-{i}" for i in range(5)]
        capped = replay(db, tenant_id="tenant-w", device_id="dev-w", after_id=0, max_events=2)
        assert [e["data"]["request_id"] for e in capped] == ["w-0", "w-1"]
        wide = replay(db, tenant_id="tenant-w", device_id="dev-w", after_id=0, window_seconds=48 * 3600)
        assert wide[0]["data"]["request_id"] == "w-old"


def test_parse_last_event_id_accepts_only_non_negative_integers():
    assert parse_last_event_id(None) is None
    assert parse_last_event_id("") is None
    assert parse_last_event_id("abc") is None
    assert parse_last_event_id("-1") is None
    assert parse_last_event_id(" 42 ") == 42
    assert parse_last_event_id("0") == 0
