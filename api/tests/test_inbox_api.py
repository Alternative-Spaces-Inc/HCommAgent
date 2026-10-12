import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_inbox.db")

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.models import DEFAULT_TENANT_ID
from app.db import SessionLocal
from app import models
from authhelper import agent_auth
from deviceauth import human_get, human_post


client = TestClient(app)


def _request(title, deadline_at=None, red_gate=False):
    body = {
        "caller": "test-agent",
        "audience": "owner",
        "title": title,
        "control": "radio",
        "options_or_fields": [{"id": "a", "label": "A"}],
        "suggested": {"option_id": "a", "why": "only option"},
        "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
    }
    if deadline_at:
        body["deadline_at"] = deadline_at.isoformat()
    if red_gate:
        body["red_gate"] = {"resource": "prod-db", "change": "drop table", "impact": "high"}
    return body


def test_inbox_lists_only_pending_sorted_by_deadline_and_impact():
    now = datetime.now(timezone.utc)

    resp = client.post("/v1/requests", json=_request("no deadline"), headers=agent_auth("test-agent"))
    assert resp.status_code == 201

    resp = client.post("/v1/requests", json=_request("far deadline", deadline_at=now + timedelta(hours=5)), headers=agent_auth("test-agent"))
    assert resp.status_code == 201

    resp = client.post("/v1/requests", json=_request("near deadline red gate", deadline_at=now + timedelta(hours=1), red_gate=True), headers=agent_auth("test-agent"))
    assert resp.status_code == 201

    resp = client.post("/v1/requests", json=_request("near deadline plain", deadline_at=now + timedelta(hours=1)), headers=agent_auth("test-agent"))
    assert resp.status_code == 201

    # answer one so it drops out of the pending inbox
    resp = client.post("/v1/requests", json=_request("already answered"), headers=agent_auth("test-agent"))
    answered_id = resp.json()["request_id"]
    human_post(client, f"/v1/requests/{answered_id}/answer", json={"control": "radio", "value": "a", "surface": "browser_overlay"})

    resp = human_get(client, "/v1/inbox")
    assert resp.status_code == 200
    ids = [item["title"] for item in resp.json()]

    assert "already answered" not in ids
    # every inbox item carries the request_id the surface answers against
    for item in resp.json():
        assert item["request_id"], f"inbox item {item['title']!r} has no request_id"
    assert answered_id not in [item["request_id"] for item in resp.json()]
    # near deadlines first, red gate ahead of plain at the same deadline, no-deadline last
    assert ids.index("near deadline red gate") < ids.index("near deadline plain")
    assert ids.index("near deadline plain") < ids.index("far deadline")
    assert ids.index("far deadline") < ids.index("no deadline")


def test_inbox_stream_route_is_registered_as_sse():
    # Starlette's TestClient does not stream an open-ended async generator
    # body incrementally (it was observed to hang indefinitely trying to
    # open the connection for this endpoint), so the route contract is
    # checked without invoking the live generator over HTTP: the route
    # exists at the right path/method and declares text/event-stream.
    # Read the schema rather than app.routes: FastAPI 0.143 wraps included
    # routers in a container object whose route list is not flat.
    paths = app.openapi()["paths"]
    assert "/v1/inbox/stream" in paths, "GET /v1/inbox/stream is not registered"
    assert "get" in paths["/v1/inbox/stream"]

    import inspect
    from app import inbox_api
    assert "device" in inspect.signature(inbox_api.stream_inbox).parameters
    src = inspect.getsource(inbox_api.stream_inbox)
    assert "text/event-stream" in src


def _stream_device():
    """HCA-P19: the stream generator is bound to an authenticated device."""
    from deviceauth import ensure_registered, DEVICE_ID
    ensure_registered()
    with SessionLocal() as db:
        d = db.get(models.Device, DEVICE_ID)
        db.expunge(d)
        return d


def test_inbox_event_source_first_chunk_is_a_connected_comment():
    import asyncio
    from app.inbox_api import inbox_event_source

    async def run():
        gen = inbox_event_source(_stream_device(), keepalive_seconds=5)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        assert first == ": connected\n\n"
        await gen.aclose()

    asyncio.run(run())


def test_inbox_event_source_delivers_a_published_event():
    import asyncio
    from app.inbox_api import inbox_event_source
    from app.events import publish

    async def run():
        gen = inbox_event_source(_stream_device(), keepalive_seconds=5)
        await gen.__anext__()  # the ": connected" comment
        publish("request.cancelled", {"request_id": "xyz"}, tenant_id=DEFAULT_TENANT_ID)
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=1)
        assert chunk.startswith("id: ")
        assert "\nevent: request.cancelled\n" in chunk
        assert "xyz" in chunk
        await gen.aclose()

    asyncio.run(run())


# HCA-P20 Day 1: a reconnecting stream replays what it missed.

def _parse_sse(chunk: str) -> dict:
    import json
    out = {}
    for line in chunk.strip().splitlines():
        k, _, v = line.partition(": ")
        out[k] = json.loads(v) if k == "data" else v
    return out


def test_stream_with_last_event_id_replays_missed_events_then_goes_live():
    import asyncio
    from app.inbox_api import inbox_event_source
    from app.events import publish

    device = _stream_device()

    async def run():
        # Session 1: see one event, note its id, drop the connection.
        gen = inbox_event_source(device, keepalive_seconds=5)
        await gen.__anext__()
        publish("request.cancelled", {"request_id": "seen"}, tenant_id=DEFAULT_TENANT_ID)
        seen = _parse_sse(await asyncio.wait_for(gen.__anext__(), timeout=1))
        await gen.aclose()

        # While disconnected: two events the device should get, one addressed
        # to another device it must not get.
        publish("request.cancelled", {"request_id": "missed-1"}, tenant_id=DEFAULT_TENANT_ID)
        publish("request.created", {"request_id": "not-mine"}, tenant_id=DEFAULT_TENANT_ID, device_ids=["some-other-device"])
        publish("request.created", {"request_id": "missed-2"}, tenant_id=DEFAULT_TENANT_ID, device_ids=[device.id])

        # Session 2: reconnect with Last-Event-ID.
        gen2 = inbox_event_source(device, keepalive_seconds=5, last_event_id=int(seen["id"]))
        assert await gen2.__anext__() == ": connected\n\n"
        r1 = _parse_sse(await asyncio.wait_for(gen2.__anext__(), timeout=1))
        r2 = _parse_sse(await asyncio.wait_for(gen2.__anext__(), timeout=1))
        assert [r1["data"]["data"]["request_id"], r2["data"]["data"]["request_id"]] == ["missed-1", "missed-2"]
        assert int(seen["id"]) < int(r1["id"]) < int(r2["id"])
        # Live delivery resumes after the replay, with no duplicate of r2.
        publish("request.answered", {"request_id": "live"}, tenant_id=DEFAULT_TENANT_ID)
        live = _parse_sse(await asyncio.wait_for(gen2.__anext__(), timeout=1))
        assert live["data"]["data"]["request_id"] == "live"
        assert int(live["id"]) > int(r2["id"])
        await gen2.aclose()

    asyncio.run(run())


def test_stream_without_last_event_id_replays_nothing():
    import asyncio
    from app.inbox_api import inbox_event_source
    from app.events import publish

    device = _stream_device()
    publish("request.cancelled", {"request_id": "before-connect"}, tenant_id=DEFAULT_TENANT_ID)

    async def run():
        gen = inbox_event_source(device, keepalive_seconds=0.2)
        await gen.__anext__()
        nxt = await asyncio.wait_for(gen.__anext__(), timeout=1)
        assert nxt == ": keep-alive\n\n", f"an event was replayed without Last-Event-ID: {nxt!r}"
        await gen.aclose()

    asyncio.run(run())


def test_stream_route_reads_last_event_id_header_and_since_query():
    import inspect
    from app import inbox_api
    params = inspect.signature(inbox_api.stream_inbox).parameters
    assert "last_event_id_header" in params and "since" in params
    spec = app.openapi()["paths"]["/v1/inbox/stream"]["get"]
    names = {(p["in"], p["name"]) for p in spec.get("parameters", [])}
    assert ("header", "last-event-id") in names or ("header", "Last-Event-ID") in names, names
    assert ("query", "since") in names


# HCA-P20 Day 5: a device keeps at most STREAM_MAX_PER_DEVICE open streams on
# an instance; the oldest is closed with stream.closed reason superseded. Found
# live: one extension device held 46 streams on one instance (metrics page).

def test_fifth_stream_of_a_device_supersedes_the_oldest(monkeypatch):
    import asyncio
    from app import events
    from app.inbox_api import inbox_event_source
    from deviceauth import DEVICE_ID

    monkeypatch.setattr(events, "STREAM_MAX_PER_DEVICE", 4)
    before = events.stream_state["superseded"]

    async def run():
        gens = []
        for _ in range(4):
            g = inbox_event_source(_stream_device(), keepalive_seconds=5)
            assert await asyncio.wait_for(g.__anext__(), timeout=1) == ": connected\n\n"
            gens.append(g)
        assert sum(1 for p in events.stream_positions() if p["device_id"] == DEVICE_ID) == 4

        fifth = inbox_event_source(_stream_device(), keepalive_seconds=5)
        assert await asyncio.wait_for(fifth.__anext__(), timeout=1) == ": connected\n\n"

        # the oldest stream is told to close and its generator ends
        closing = await asyncio.wait_for(gens[0].__anext__(), timeout=1)
        assert "event: stream.closed" in closing and "superseded" in closing
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gens[0].__anext__(), timeout=1)

        # the other four stay open and still receive events
        events.publish("request.cancelled", {"request_id": "cap-1"}, tenant_id=DEFAULT_TENANT_ID)
        for g in gens[1:] + [fifth]:
            chunk = await asyncio.wait_for(g.__anext__(), timeout=1)
            assert "cap-1" in chunk
        assert sum(1 for p in events.stream_positions() if p["device_id"] == DEVICE_ID) == 4
        assert events.stream_state["superseded"] == before + 1
        for g in gens[1:] + [fifth]:
            await g.aclose()

    asyncio.run(run())


def test_stream_lag_starts_at_the_dispatcher_cursor_not_zero():
    import asyncio
    from app import events
    from app.inbox_api import inbox_event_source
    from deviceauth import DEVICE_ID

    events.publish("request.cancelled", {"request_id": "lag-0"}, tenant_id=DEFAULT_TENANT_ID)
    newest = events.max_event_id()

    async def run():
        g = inbox_event_source(_stream_device(), keepalive_seconds=5)
        await asyncio.wait_for(g.__anext__(), timeout=1)
        mine = [p for p in events.stream_positions() if p["device_id"] == DEVICE_ID]
        assert mine and all(p["last_sent_id"] >= newest for p in mine), mine
        await g.aclose()

    asyncio.run(run())
