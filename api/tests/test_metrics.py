"""HCA-P20 Day 4: the metrics page (app/metrics.py) and the counters it reads."""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_local.db")

from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine, SessionLocal
from app import events, metrics, models
from authhelper import ADMIN_AUTH, agent_auth

Base.metadata.create_all(bind=engine)
client = TestClient(app)
TENANT = models.DEFAULT_TENANT_ID


def test_metrics_route_is_admin_only_and_has_every_section():
    assert client.get("/v1/internal/metrics").status_code == 401
    assert client.get("/v1/internal/metrics", headers={"Authorization": "Bearer nope"}).status_code == 403
    page = client.get("/v1/internal/metrics", headers=ADMIN_AUTH)
    assert page.status_code == 200
    body = page.json()
    assert set(body) >= {"at", "events", "streams", "drops", "replay", "listener", "jobs", "db_pool", "instance"}
    assert body["jobs"]["due_backlog"] >= 0 and body["jobs"]["oldest_due_age_seconds"] >= 0
    assert body["db_pool"]["class"]
    assert body["instance"]["pid"] == os.getpid()


def test_stream_position_and_lag_follow_what_was_sent():
    async def run():
        q = events.subscribe("dev_metrics", TENANT)
        try:
            # HCA-P20 Day 5: a position starts at the dispatcher cursor (the
            # newest id when the stream connected), so an idle stream reads
            # as caught up rather than as newest_id events behind.
            start = events.stream_positions()
            pos = [p for p in start if p["device_id"] == "dev_metrics"]
            cursor = events.max_event_id()
            assert pos and pos[0]["last_sent_id"] == cursor
            events.mark_sent(q, cursor + 5)
            events.mark_sent(q, cursor + 3)  # never goes backwards
            pos = [p for p in events.stream_positions() if p["device_id"] == "dev_metrics"]
            assert pos and pos[0]["last_sent_id"] == cursor + 5
            with SessionLocal() as db:
                page = metrics.collect(db)
            assert page["streams"]["open"] >= 1
            assert page["streams"]["max_per_device"] == events.STREAM_MAX_PER_DEVICE
            assert "superseded" in page["streams"]
        finally:
            events.unsubscribe(q)
        assert not [p for p in events.stream_positions() if p["device_id"] == "dev_metrics"]
    asyncio.run(run())


def test_replay_counts_a_gap_when_truncated_and_none_when_complete():
    before = dict(events.replay_state)
    with SessionLocal() as db:
        start = db.execute(events.select(events.func.max(models.Event.id))).scalar() or 0
    for i in range(4):
        events.publish("request.cancelled", {"request_id": f"metrics-{i}", "state": "cancelled"}, tenant_id=TENANT)
    with SessionLocal() as db:
        full = events.replay(db, tenant_id=TENANT, device_id="dev_metrics", after_id=start)
        assert len(full) >= 4
        cut = events.replay(db, tenant_id=TENANT, device_id="dev_metrics", after_id=start, max_events=2)
        assert len(cut) == 2
    after = dict(events.replay_state)
    assert after["replays"] == before["replays"] + 2
    assert after["truncated"] == before["truncated"] + 1
    assert after["gaps"] == before["gaps"] + 1, "only the truncated replay is a gap"
    assert after["replayed_events"] >= before["replayed_events"] + 6


def test_replay_counts_a_gap_when_the_window_aged_rows_out():
    """Ids grow with time, so rows that aged out of the window are the lowest
    ids. Age the two lowest rows and reconnect from before them."""
    before = dict(events.replay_state)
    events.publish("request.cancelled", {"request_id": "metrics-new", "state": "cancelled"}, tenant_id=TENANT)
    with SessionLocal() as db:
        lowest = db.execute(events.select(models.Event).order_by(models.Event.id.asc()).limit(2)).scalars().all()
        assert len(lowest) == 2
        for row in lowest:
            row.created_at = (datetime.now(timezone.utc) - timedelta(days=3)).replace(tzinfo=None)
        db.commit()
        aged_ids = [r.id for r in lowest]
        got = events.replay(db, tenant_id=TENANT, device_id="dev_metrics", after_id=0, window_seconds=3600)
    assert all(e["id"] not in aged_ids for e in got), "aged out rows are not replayed"
    assert events.replay_state["gaps"] == before["gaps"] + 1
    # a reconnect from an id inside the window is complete: no gap
    with SessionLocal() as db:
        newest = db.execute(events.select(events.func.max(models.Event.id))).scalar()
        events.replay(db, tenant_id=TENANT, device_id="dev_metrics", after_id=newest - 1, window_seconds=3600)
    assert events.replay_state["gaps"] == before["gaps"] + 1


def test_jobs_backlog_counts_due_undone_rows():
    # own ask row, so the test does not depend on an earlier module having posted one
    body = {"caller": "metrics-agent", "audience": "owner", "title": "backlog ask", "control": "radio",
            "options_or_fields": [{"id": "a", "label": "A"}], "suggested": {"option_id": "a", "why": "w"},
            "deliver_to": {"mode": "poll"}}
    r = client.post("/v1/requests", json=body, headers=agent_auth("metrics-agent"))
    assert r.status_code == 201, r.text
    rid = r.json()["request_id"]
    with SessionLocal() as db:
        ask = db.get(models.Ask, rid)
        assert ask is not None
        past = (datetime.now(timezone.utc) - timedelta(seconds=120)).replace(tzinfo=None)
        job = models.ScheduledJob(tenant_id=ask.tenant_id, ask_id=ask.id, kind="expire", run_at=past, created_at=past)
        db.add(job)
        db.commit()
        page = metrics.collect(db)
        assert page["jobs"]["due_backlog"] >= 1 and page["jobs"]["oldest_due_age_seconds"] >= 119
        db.delete(db.get(models.ScheduledJob, job.id))
        db.commit()
