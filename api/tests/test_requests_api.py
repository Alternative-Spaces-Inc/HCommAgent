import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_api.db")

from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine
from authhelper import agent_auth, ADMIN_AUTH
from deviceauth import human_get, human_post

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)


def _sample_request():
    return {
        "caller": "test-agent",
        "audience": "owner",
        "title": "Pick a color",
        "why": "need a decision",
        "control": "radio",
        "options_or_fields": [
            {"id": "red", "label": "Red"},
            {"id": "blue", "label": "Blue"},
        ],
        "suggested": {"option_id": "red", "why": "brighter"},
        "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
    }


def test_create_and_get_request():
    resp = client.post("/v1/requests", json=_sample_request(), headers=agent_auth("test-agent"))
    assert resp.status_code == 201
    receipt = resp.json()
    assert receipt["state"] == "queued"
    request_id = receipt["request_id"]

    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("test-agent"))
    assert resp.status_code == 200
    view = resp.json()
    assert view["state"] == "queued"
    assert view["request"]["title"] == "Pick a color"
    assert view["answer"] is None


def test_answer_then_409_on_second_answer():
    resp = client.post("/v1/requests", json=_sample_request(), headers=agent_auth("test-agent"))
    request_id = resp.json()["request_id"]

    resp = human_post(client, f"/v1/requests/{request_id}/answer",
        json={"control": "radio", "value": "blue", "surface": "browser_overlay"},
    )
    assert resp.status_code == 201
    record = resp.json()
    assert record["value"] == "blue"
    assert record["request_id"] == request_id

    resp = client.get(f"/v1/requests/{request_id}", headers=agent_auth("test-agent"))
    assert resp.json()["state"] == "answered"
    assert resp.json()["answer"]["value"] == "blue"

    resp = human_post(client, f"/v1/requests/{request_id}/answer",
        json={"control": "radio", "value": "red", "surface": "browser_overlay"},
    )
    assert resp.status_code == 409


def test_answer_422_on_bad_option_id():
    resp = client.post("/v1/requests", json=_sample_request(), headers=agent_auth("test-agent"))
    request_id = resp.json()["request_id"]

    resp = human_post(client, f"/v1/requests/{request_id}/answer",
        json={"control": "radio", "value": "green", "surface": "browser_overlay"},
    )
    assert resp.status_code == 422
