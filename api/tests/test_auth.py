import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_auth.db")

from fastapi.testclient import TestClient

from app.main import app
from authhelper import agent_auth, agent_key, ADMIN_AUTH
from deviceauth import human_get, human_post


client = TestClient(app)


def _ask(caller):
    return {
        "caller": caller,
        "audience": "owner",
        "title": "guarded ask",
        "control": "radio",
        "options_or_fields": [{"id": "a", "label": "A"}],
        "suggested": {"option_id": "a", "why": "only option"},
        "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
    }


def test_create_without_bearer_is_401():
    resp = client.post("/v1/requests", json=_ask("agent-auth-1"))
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_create_with_garbage_key_is_403():
    resp = client.post("/v1/requests", json=_ask("agent-auth-1"), headers={"Authorization": "Bearer hca_live_nope_nope"})
    assert resp.status_code == 403


def test_create_as_another_caller_is_403():
    resp = client.post("/v1/requests", json=_ask("agent-auth-victim"), headers=agent_auth("agent-auth-1"))
    assert resp.status_code == 403
    assert "caller" in resp.json()["detail"]


def test_create_read_cancel_with_own_key_succeed():
    resp = client.post("/v1/requests", json=_ask("agent-auth-1"), headers=agent_auth("agent-auth-1"))
    assert resp.status_code == 201
    rid = resp.json()["request_id"]
    assert client.get(f"/v1/requests/{rid}", headers=agent_auth("agent-auth-1")).status_code == 200
    assert client.post(f"/v1/requests/{rid}/cancel", json={"reason": "done"}, headers=agent_auth("agent-auth-1")).status_code == 200


def test_another_callers_ask_reads_and_cancels_as_404():
    rid = client.post("/v1/requests", json=_ask("agent-auth-1"), headers=agent_auth("agent-auth-1")).json()["request_id"]
    other = agent_auth("agent-auth-2")
    assert client.get(f"/v1/requests/{rid}", headers=other).status_code == 404
    assert client.post(f"/v1/requests/{rid}/cancel", json={"reason": "not mine"}, headers=other).status_code == 404
    # and the owner still sees it untouched
    assert client.get(f"/v1/requests/{rid}", headers=agent_auth("agent-auth-1")).json()["state"] == "queued"


def test_reissued_key_revokes_the_old_one():
    old = agent_key("agent-auth-rotate")
    resp = client.post("/v1/api-keys", json={"caller": "agent-auth-rotate"}, headers=ADMIN_AUTH)
    assert resp.status_code == 201
    new = resp.json()["api_key"]
    assert client.post("/v1/requests", json=_ask("agent-auth-rotate"), headers={"Authorization": f"Bearer {old}"}).status_code == 403
    assert client.post("/v1/requests", json=_ask("agent-auth-rotate"), headers={"Authorization": f"Bearer {new}"}).status_code == 201


def test_human_surface_routes_require_a_device_signature():
    # HCA-P6: the human routes are closed. Unsigned calls are 401 with the
    # HCA-Device challenge; signed calls from a registered device work.
    rid = client.post("/v1/requests", json=_ask("agent-auth-1"), headers=agent_auth("agent-auth-1")).json()["request_id"]
    r = client.get("/v1/inbox")
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "HCA-Device"
    assert client.post(f"/v1/requests/{rid}/answer", json={"control": "radio", "value": "a", "surface": "browser_overlay"}).status_code == 401
    assert client.get("/v1/audit").status_code == 401
    assert human_get(client, "/v1/inbox").status_code == 200
    assert human_post(client, f"/v1/requests/{rid}/answer", json={"control": "radio", "value": "a", "surface": "browser_overlay"}).status_code == 201
    assert human_get(client, "/v1/audit").status_code == 200


def test_api_key_issue_without_admin_bearer_is_401_and_wrong_is_403():
    assert client.post("/v1/api-keys", json={"caller": "x"}).status_code == 401
    assert client.post("/v1/api-keys", json={"caller": "x"}, headers={"Authorization": "Bearer wrong"}).status_code == 403


def test_api_key_issue_fails_closed_when_admin_token_unset(monkeypatch):
    monkeypatch.delenv("HCA_ADMIN_TOKEN", raising=False)
    resp = client.post("/v1/api-keys", json={"caller": "x"}, headers=ADMIN_AUTH)
    assert resp.status_code == 403
    assert "not configured" in resp.json()["detail"]


def test_device_dependency_leaves_no_open_transaction():
    """HCA-P19 hotfix: after require_device commits last_seen_at, reading the
    device's attributes (require_linked_device, the SSE stream) must not
    re-select the row and open a transaction the stream then holds for
    hours (it blocked the P19 Day 1 ALTER TABLE on 2026-10-10)."""
    import asyncio
    from app.db import SessionLocal
    from app.auth import require_device
    from app import models
    from deviceauth import ensure_registered, human_headers
    from starlette.requests import Request
    ensure_registered()
    headers = human_headers("GET", "/v1/inbox", b"")
    scope = {"type": "http", "method": "GET", "path": "/v1/inbox", "query_string": b"", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    db = SessionLocal()
    try:
        device = asyncio.run(require_device(Request(scope, receive), db, None))
        assert not db.in_transaction()
        _ = (device.google_sub, device.tenant_id, device.id)
        assert not db.in_transaction(), "attribute reads after commit must not start a transaction"
        assert isinstance(device, models.Device)
    finally:
        db.close()
