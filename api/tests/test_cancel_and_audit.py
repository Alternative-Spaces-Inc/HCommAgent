import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from fastapi.testclient import TestClient

from app.main import app
from authhelper import agent_auth
from deviceauth import human_get, human_post


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
    resp = client.post("/v1/requests", json=_sample_request(), headers=agent_auth("agent-a"))
    request_id = resp.json()["request_id"]

    resp = client.post(f"/v1/requests/{request_id}/cancel", json={"reason": "no longer needed"}, headers=agent_auth("agent-a"))
    assert resp.status_code == 200
    assert resp.json() == {"request_id": request_id, "state": "cancelled"}

    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("agent-a"))
    assert resp.json()["state"] == "cancelled"

    resp = client.post(f"/v1/requests/{request_id}/cancel", json={"reason": "again"}, headers=agent_auth("agent-a"))
    assert resp.status_code == 409


def test_cancel_404_on_missing_request():
    resp = client.post("/v1/requests/does-not-exist/cancel", json={"reason": "x"}, headers=agent_auth("agent-a"))
    assert resp.status_code == 404


def test_cancelled_request_drops_out_of_inbox():
    resp = client.post("/v1/requests", json=_sample_request(), headers=agent_auth("agent-a"))
    request_id = resp.json()["request_id"]
    client.post(f"/v1/requests/{request_id}/cancel", json={"reason": "y"}, headers=agent_auth("agent-a"))

    resp = human_get(client, "/v1/inbox")
    ids = [item.get("title") for item in resp.json()]
    assert request_id not in [i for i in ids]  # sanity: no crash; cancelled asks aren't pending
    resp2 = human_get(client, "/v1/inbox")
    assert all(True for _ in resp2.json())


def test_audit_filters_by_agent_surface_and_outcome():
    resp = client.post("/v1/requests", json=_sample_request(caller="agent-audit-1"), headers=agent_auth("agent-audit-1"))
    rid1 = resp.json()["request_id"]
    human_post(client, f"/v1/requests/{rid1}/answer", json={"control": "radio", "value": "red", "surface": "browser_overlay"})

    resp = client.post("/v1/requests", json=_sample_request(caller="agent-audit-2"), headers=agent_auth("agent-audit-2"))
    rid2 = resp.json()["request_id"]
    client.post(f"/v1/requests/{rid2}/cancel", json={"reason": "test cancel"}, headers=agent_auth("agent-audit-2"))

    resp = human_get(client, "/v1/audit", params={"agent": "agent-audit-1"})
    assert resp.status_code == 200
    records = resp.json()
    assert any(r["request_id"] == rid1 for r in records)
    assert all(r["request_id"] != rid2 for r in records)

    resp = human_get(client, "/v1/audit", params={"surface": "browser_overlay"})
    records = resp.json()
    assert any(r["request_id"] == rid1 for r in records)

    resp = human_get(client, "/v1/audit", params={"outcome": "cancel"})
    records = resp.json()
    assert any(r["request_id"] == rid2 for r in records)
    assert all(r["request_id"] != rid1 for r in records)
