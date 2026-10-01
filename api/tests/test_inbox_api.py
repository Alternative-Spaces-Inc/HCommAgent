import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_inbox.db")

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine
from authhelper import agent_auth, ADMIN_AUTH
from deviceauth import human_get, human_post

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

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
    no_deadline_id = resp.json()["request_id"]

    resp = client.post("/v1/requests", json=_request("far deadline", deadline_at=now + timedelta(hours=5)), headers=agent_auth("test-agent"))
    far_id = resp.json()["request_id"]

    resp = client.post("/v1/requests", json=_request("near deadline red gate", deadline_at=now + timedelta(hours=1), red_gate=True), headers=agent_auth("test-agent"))
    near_red_gate_id = resp.json()["request_id"]

    resp = client.post("/v1/requests", json=_request("near deadline plain", deadline_at=now + timedelta(hours=1)), headers=agent_auth("test-agent"))
    near_plain_id = resp.json()["request_id"]

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
    matches = [r for r in app.routes if getattr(r, "path", None) == "/v1/inbox/stream"]
    assert matches, "GET /v1/inbox/stream is not registered"
    route = matches[0]
    assert "GET" in route.methods

    import inspect
    from app import inbox_api
    sig = inspect.signature(inbox_api.stream_inbox)
    src = inspect.getsource(inbox_api.stream_inbox)
    assert "text/event-stream" in src


def test_inbox_event_source_first_chunk_is_a_connected_comment():
    import asyncio
    from app.inbox_api import inbox_event_source

    async def run():
        gen = inbox_event_source(keepalive_seconds=5)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        assert first == ": connected\n\n"
        await gen.aclose()

    asyncio.run(run())


def test_inbox_event_source_delivers_a_published_event():
    import asyncio
    from app.inbox_api import inbox_event_source
    from app.events import publish

    async def run():
        gen = inbox_event_source(keepalive_seconds=5)
        await gen.__anext__()  # the ": connected" comment
        publish("request.cancelled", {"request_id": "xyz"})
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=1)
        assert chunk.startswith("event: request.cancelled\n")
        assert "xyz" in chunk
        await gen.aclose()

    asyncio.run(run())
