import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_api.db")

from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine
from authhelper import agent_auth
from deviceauth import human_post

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


def test_answer_race_loser_gets_409_not_500(monkeypatch):
    # Simulate two answers committing in the same instant: the second
    # commit hits the unique constraint on audit_rows.ask_id.
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session
    resp = client.post("/v1/requests", json=_sample_request(), headers=agent_auth("test-agent"))
    request_id = resp.json()["request_id"]
    real_commit = Session.commit
    calls = {"n": 0}

    def racing_commit(self):
        from app import models
        pending_audit = any(isinstance(o, models.AuditRow) for o in self.new)
        if pending_audit and calls["n"] == 0:
            calls["n"] += 1
            raise IntegrityError("INSERT INTO audit_rows", {}, Exception("duplicate key value violates unique constraint audit_rows_ask_id_key"))
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", racing_commit)
    resp = human_post(client, f"/v1/requests/{request_id}/answer",
                      json={"control": "radio", "value": "blue", "surface": "browser_overlay"})
    assert resp.status_code == 409
    # HCA-P19 Day 2: every losing transition gets the same 409 shape; the
    # simulated race has no real second writer, so the re-read state is
    # still queued.
    detail = resp.json()["detail"]
    assert detail["error"] == "request_terminal" and detail["request_id"] == request_id
    monkeypatch.undo()
    # the ask is untouched, so a real answer still lands
    resp = human_post(client, f"/v1/requests/{request_id}/answer",
                      json={"control": "radio", "value": "blue", "surface": "browser_overlay"})
    assert resp.status_code == 201
