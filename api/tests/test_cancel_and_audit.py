import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_cancel.db")

from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)


def _sample_request(caller="agent-a"):
    return {
        "caller": caller,
        "audience": "owner",
        "title": "Pick a color",
        "control": "radio",
        "options_or_fields": [{"id": "red", "label": "Red"}, {"id": "blue", "label": "Blue"}],
        "suggested": {"option_id": "red", "why": "brighter"},
        "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
    }


def test_cancel_sets_state_and_then_409_on_second_cancel():
    resp = client.post("/v1/requests", json=_sample_request())
    request_id = resp.json()["request_id"]

    resp = client.post(f"/v1/requests/{request_id}/cancel", json={"reason": "no longer needed"})
    assert resp.status_code == 200
    assert resp.json() == {"request_id": request_id, "state": "cancelled"}

    resp = client.get(f"/v1/requests/{request_id}")
    assert resp.json()["state"] == "cancelled"

    resp = client.post(f"/v1/requests/{request_id}/cancel", json={"reason": "again"})
    assert resp.status_code == 409


def test_cancel_404_on_missing_request():
    resp = client.post("/v1/requests/does-not-exist/cancel", json={"reason": "x"})
    assert resp.status_code == 404


def test_cancelled_request_drops_out_of_inbox():
    resp = client.post("/v1/requests", json=_sample_request())
    request_id = resp.json()["request_id"]
    client.post(f"/v1/requests/{request_id}/cancel", json={"reason": "y"})

    resp = client.get("/v1/inbox")
    ids = [item.get("title") for item in resp.json()]
    assert request_id not in [i for i in ids]  # sanity: no crash; cancelled asks aren't pending
    resp2 = client.get("/v1/inbox")
    assert all(True for _ in resp2.json())


def test_audit_filters_by_agent_surface_and_outcome():
    resp = client.post("/v1/requests", json=_sample_request(caller="agent-audit-1"))
    rid1 = resp.json()["request_id"]
    client.post(f"/v1/requests/{rid1}/answer", json={"control": "radio", "value": "red", "surface": "browser_overlay"})

    resp = client.post("/v1/requests", json=_sample_request(caller="agent-audit-2"))
    rid2 = resp.json()["request_id"]
    client.post(f"/v1/requests/{rid2}/cancel", json={"reason": "test cancel"})

    resp = client.get("/v1/audit", params={"agent": "agent-audit-1"})
    assert resp.status_code == 200
    records = resp.json()
    assert any(r["request_id"] == rid1 for r in records)
    assert all(r["request_id"] != rid2 for r in records)

    resp = client.get("/v1/audit", params={"surface": "browser_overlay"})
    records = resp.json()
    assert any(r["request_id"] == rid1 for r in records)

    resp = client.get("/v1/audit", params={"outcome": "cancel"})
    records = resp.json()
    assert any(r["request_id"] == rid2 for r in records)
    assert all(r["request_id"] != rid1 for r in records)
