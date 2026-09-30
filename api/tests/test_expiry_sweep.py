import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_expiry.db")

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine, SessionLocal
from app.expiry import run_expiry_sweep
from app import models
from authhelper import agent_auth
from deviceauth import human_get, human_post

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)


def _create(caller, deadline_at, expiry_policy=None):
    body = {
        "caller": caller,
        "audience": "owner",
        "title": f"ask from {caller}",
        "control": "radio",
        "options_or_fields": [{"id": "a", "label": "A"}],
        "suggested": {"option_id": "a", "why": "only option"},
        "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
        "deadline_at": deadline_at.isoformat(),
    }
    if expiry_policy:
        body["expiry_policy"] = expiry_policy
    resp = client.post("/v1/requests", json=body, headers=agent_auth(caller))
    assert resp.status_code == 201
    return resp.json()["request_id"]


def test_default_auto_deny_writes_a_deny_answer_record_and_expires():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    request_id = _create("agent-default", past)

    db = SessionLocal()
    try:
        results = run_expiry_sweep(db)
    finally:
        db.close()

    matching = [r for r in results if r["request_id"] == request_id]
    assert matching == [{"request_id": request_id, "expiry_policy": "auto_deny", "action": "auto_denied"}]

    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("agent-default"))
    body = resp.json()
    assert body["state"] == "expired"
    assert body["answer"] is not None
    assert body["answer"]["value"]["decision"] == "deny"
    assert body["answer"]["answered_by"] == "system:expiry_sweep"

    resp = human_get(client, "/v1/audit", params={"agent": "agent-default", "outcome": "deny"})
    assert any(r["request_id"] == request_id for r in resp.json())


def test_expire_policy_expires_with_no_answer_recorded():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    request_id = _create("agent-expire", past, expiry_policy="expire")

    db = SessionLocal()
    try:
        results = run_expiry_sweep(db)
    finally:
        db.close()

    matching = [r for r in results if r["request_id"] == request_id]
    assert matching == [{"request_id": request_id, "expiry_policy": "expire", "action": "expired"}]

    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("agent-expire"))
    body = resp.json()
    assert body["state"] == "expired"
    assert body["answer"] is None


def test_hold_policy_leaves_the_ask_untouched():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    request_id = _create("agent-hold", past, expiry_policy="hold")

    db = SessionLocal()
    try:
        results = run_expiry_sweep(db)
    finally:
        db.close()

    matching = [r for r in results if r["request_id"] == request_id]
    assert matching == [{"request_id": request_id, "expiry_policy": "hold", "action": "held"}]

    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("agent-hold"))
    body = resp.json()
    assert body["state"] == "queued"


def test_future_deadline_is_not_swept():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    request_id = _create("agent-future", future)

    db = SessionLocal()
    try:
        results = run_expiry_sweep(db)
    finally:
        db.close()

    assert all(r["request_id"] != request_id for r in results)
    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("agent-future"))
    assert resp.json()["state"] == "queued"


def test_no_deadline_never_swept():
    resp = client.post("/v1/requests", json={
        "caller": "agent-no-deadline",
        "audience": "owner",
        "title": "no deadline ever",
        "control": "radio",
        "options_or_fields": [{"id": "a", "label": "A"}],
        "suggested": {"option_id": "a", "why": "only option"},
        "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
    }, headers=agent_auth("agent-no-deadline"))
    request_id = resp.json()["request_id"]

    db = SessionLocal()
    try:
        results = run_expiry_sweep(db)
    finally:
        db.close()

    assert all(r["request_id"] != request_id for r in results)
    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("agent-no-deadline"))
    assert resp.json()["state"] == "queued"


def test_terminal_states_are_never_reswept():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    request_id = _create("agent-answered", past)
    human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "radio", "value": "a", "surface": "browser_overlay"})

    db = SessionLocal()
    try:
        results = run_expiry_sweep(db)
    finally:
        db.close()

    assert all(r["request_id"] != request_id for r in results)
    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("agent-answered"))
    assert resp.json()["state"] == "answered"
