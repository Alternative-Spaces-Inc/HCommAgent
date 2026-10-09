import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_webhooks.db")

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import Base, engine, SessionLocal
from authhelper import agent_auth
from app import models, webhooks
from app.expiry import run_expiry_sweep
from deviceauth import human_post

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)


def _sample(url="https://example.test/hook", mode="webhook", **extra):
    body = {
        "caller": "loop-agent",
        "audience": "owner",
        "title": "Ship it?",
        "control": "approve_deny",
        "options_or_fields": [
            {"id": "approve", "label": "Approve"},
            {"id": "deny", "label": "Deny"},
        ],
        "suggested": {"option_id": "approve", "why": "checks are green"},
        "deliver_to": {"mode": mode, "url": url},
    }
    body.update(extra)
    return body


def _attempts(request_id):
    with SessionLocal() as db:
        return db.execute(
            select(models.WebhookAttempt).where(models.WebhookAttempt.ask_id == request_id).order_by(models.WebhookAttempt.attempt)
        ).scalars().all()


def test_answer_posts_request_answered_to_deliver_to(webhook_recorder):
    request_id = client.post("/v1/requests", json=_sample(), headers=agent_auth("loop-agent")).json()["request_id"]
    resp = human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "approve_deny", "value": "approve", "surface": "browser_overlay"})
    assert resp.status_code == 201

    assert len(webhook_recorder.requests) == 1
    req = webhook_recorder.requests[0]
    assert str(req.url) == "https://example.test/hook"
    assert req.headers["x-hca-event"] == "request.answered"
    assert req.headers["x-hca-request-id"] == request_id
    payload = json.loads(req.content)
    assert payload["event"] == "request.answered"
    assert payload["request_id"] == request_id
    assert payload["state"] == "answered"
    assert payload["answer"]["value"] == "approve"
    assert payload["answer"]["surface"] == "browser_overlay"

    rows = _attempts(request_id)
    assert [(r.attempt, r.status_code, r.ok) for r in rows] == [(1, 200, True)]


def test_cancel_posts_request_cancelled(webhook_recorder):
    request_id = client.post("/v1/requests", json=_sample(), headers=agent_auth("loop-agent")).json()["request_id"]
    resp = client.post(f"/v1/requests/{request_id}/cancel", json={"reason": "superseded"}, headers=agent_auth("loop-agent"))
    assert resp.status_code == 200

    assert len(webhook_recorder.requests) == 1
    payload = json.loads(webhook_recorder.requests[0].content)
    assert payload["event"] == "request.cancelled"
    assert payload["state"] == "cancelled"
    assert payload["answer"]["value"] == {"decision": "cancel", "reason": "superseded"}


def test_auto_deny_expiry_posts_request_expired_with_deny_answer(webhook_recorder):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    request_id = client.post("/v1/requests", json=_sample(deadline_at=past), headers=agent_auth("loop-agent")).json()["request_id"]

    with SessionLocal() as db:
        results = run_expiry_sweep(db)
    assert [r["action"] for r in results if r["request_id"] == request_id] == ["auto_denied"]

    matching = [r for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id]
    assert len(matching) == 1
    payload = json.loads(matching[0].content)
    assert payload["event"] == "request.expired"
    assert payload["state"] == "expired"
    assert payload["answer"]["value"]["decision"] == "deny"


def test_plain_expire_posts_request_expired_with_null_answer(webhook_recorder):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    request_id = client.post("/v1/requests", json=_sample(deadline_at=past, expiry_policy="expire"), headers=agent_auth("loop-agent")).json()["request_id"]

    with SessionLocal() as db:
        run_expiry_sweep(db)

    matching = [r for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id]
    assert len(matching) == 1
    payload = json.loads(matching[0].content)
    assert payload["event"] == "request.expired"
    assert payload["answer"] is None


def test_non_2xx_retries_up_to_max_and_records_every_attempt(webhook_recorder, monkeypatch):
    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "3")
    webhook_recorder.status_code = 503
    request_id = client.post("/v1/requests", json=_sample(), headers=agent_auth("loop-agent")).json()["request_id"]
    human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "approve_deny", "value": "deny", "surface": "web"})

    assert len(webhook_recorder.requests) == 3
    rows = _attempts(request_id)
    assert [(r.attempt, r.status_code, r.ok) for r in rows] == [(1, 503, False), (2, 503, False), (3, 503, False)]
    assert all("non-2xx" in r.error for r in rows)


def test_success_on_second_attempt_stops_retrying(webhook_recorder, monkeypatch):
    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "3")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500 if calls["n"] == 1 else 204)

    webhooks.set_transport_factory(lambda: httpx.MockTransport(handler))
    request_id = client.post("/v1/requests", json=_sample(), headers=agent_auth("loop-agent")).json()["request_id"]
    human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "approve_deny", "value": "approve", "surface": "web"})

    assert calls["n"] == 2
    rows = _attempts(request_id)
    assert [(r.attempt, r.status_code, r.ok) for r in rows] == [(1, 500, False), (2, 204, True)]


def test_connect_error_is_recorded_not_raised(webhook_recorder, monkeypatch):
    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "2")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    webhooks.set_transport_factory(lambda: httpx.MockTransport(handler))
    request_id = client.post("/v1/requests", json=_sample(), headers=agent_auth("loop-agent")).json()["request_id"]
    resp = human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "approve_deny", "value": "approve", "surface": "web"})
    assert resp.status_code == 201, "a dead receiver must never fail the human's answer"

    rows = _attempts(request_id)
    assert [(r.attempt, r.status_code, r.ok) for r in rows] == [(1, None, False), (2, None, False)]
    assert all(r.error.startswith("ConnectError") for r in rows)


def test_poll_mode_is_recorded_as_skipped(webhook_recorder):
    request_id = client.post("/v1/requests", json=_sample(mode="poll", url=None), headers=agent_auth("loop-agent")).json()["request_id"]
    human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "approve_deny", "value": "approve", "surface": "web"})

    assert webhook_recorder.requests == []
    rows = _attempts(request_id)
    assert len(rows) == 1
    assert rows[0].attempt == 0 and rows[0].ok is False
    assert "skipped" in rows[0].error
