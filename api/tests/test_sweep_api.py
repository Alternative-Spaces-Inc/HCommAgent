import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_sweep_api.db")
os.environ["SWEEP_TRIGGER_TOKEN"] = "test-sweep-token"

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine
from authhelper import agent_auth

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)


def _overdue_request():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    resp = client.post("/v1/requests", json={
        "caller": "agent-sweep-trigger",
        "audience": "owner",
        "title": "overdue ask",
        "control": "radio",
        "options_or_fields": [{"id": "a", "label": "A"}],
        "suggested": {"option_id": "a", "why": "only option"},
        "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
        "deadline_at": past.isoformat(),
    }, headers=agent_auth("agent-sweep-trigger"))
    return resp.json()["request_id"]


def test_missing_bearer_is_401():
    resp = client.post("/v1/internal/expiry-sweep")
    assert resp.status_code == 401


def test_wrong_bearer_is_403():
    resp = client.post("/v1/internal/expiry-sweep", headers={"Authorization": "Bearer not-the-token"})
    assert resp.status_code == 403


def test_correct_bearer_runs_the_sweep():
    request_id = _overdue_request()
    resp = client.post("/v1/internal/expiry-sweep", headers={"Authorization": "Bearer test-sweep-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["swept"] >= 1
    assert any(r["request_id"] == request_id for r in body["results"])

    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("agent-sweep-trigger"))
    assert resp.json()["state"] == "expired"


def test_missing_token_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("SWEEP_TRIGGER_TOKEN", raising=False)
    resp = client.post("/v1/internal/expiry-sweep", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 403
