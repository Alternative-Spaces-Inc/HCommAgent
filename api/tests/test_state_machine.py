"""HCA-P19 Day 2: strict control values, idempotency keys, one terminal
transition wins, deterministic 409.

The threaded race runs only on Postgres (DATABASE_URL starts with
postgresql), where SELECT FOR UPDATE is real; CI's Postgres job executes
it, the SQLite run skips it and keeps the sequential matrix."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app import models
from app.expiry import run_expiry_sweep
from authhelper import agent_auth
from deviceauth import human_post, ensure_registered
from overdue import backdate


client = TestClient(app)
CALLER = "sm-agent"
POSTGRES = os.environ["DATABASE_URL"].startswith("postgresql")


def _body(control="radio", options=None, **extra):
    opts = options if options is not None else [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    body = {
        "caller": CALLER, "audience": "owner", "title": f"{control} ask", "control": control,
        "options_or_fields": opts, "suggested": {"option_id": opts[0]["id"] if opts else None, "why": "test"},
        "deliver_to": {"mode": "poll"},
    }
    body.update(extra)
    return body


def _create(control="radio", options=None, headers=None, **extra):
    r = client.post("/v1/requests", json=_body(control, options, **extra), headers={**agent_auth(CALLER), **(headers or {})})
    return r


def _rid(control="radio", options=None, **extra):
    r = _create(control, options, **extra)
    assert r.status_code == 201, r.text
    return r.json()["request_id"]


def _answer(rid, control, value, headers=None):
    return human_post(client, f"/v1/requests/{rid}/answer", json={"control": control, "value": value, "surface": "browser_overlay"}, headers=headers or {})


def _state(rid):
    with SessionLocal() as db:
        return db.get(models.Ask, rid).state


# --- create time shape -------------------------------------------------------

def test_create_rejects_bad_shapes():
    ensure_registered()
    dup = [{"id": "a", "label": "A"}, {"id": "a", "label": "Again"}]
    assert _create("radio", dup).status_code == 422
    assert _create("approve_deny", [{"id": "approve", "label": "Approve"}]).status_code == 422
    assert _create("approve_deny", [{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}, {"id": "z", "label": "Z"}]).status_code == 422
    assert _create("chat", [{"id": "a", "label": "A"}]).status_code == 422
    assert _create("chat", []).status_code == 201
    assert _create("radio", [{"id": "", "label": "blank id"}]).status_code == 422
    assert _create("radio", [{"id": "a", "label": ""}]).status_code == 422
    assert _create("radio", None, title="x" * 301).status_code == 422
    many = [{"id": f"o{i}", "label": f"O{i}"} for i in range(51)]
    assert _create("checkbox", many).status_code == 422


# --- answer time value rules -------------------------------------------------

def test_answer_values_are_checked_per_control():
    rid = _rid("radio")
    assert _answer(rid, "radio", ["a"]).status_code == 422
    assert _answer(rid, "radio", "zzz").status_code == 422
    assert _answer(rid, "radio", "").status_code == 422
    assert _answer(rid, "radio", "a").status_code == 201

    rid = _rid("checkbox")
    assert _answer(rid, "checkbox", "a").status_code == 422
    assert _answer(rid, "checkbox", ["a", "a"]).status_code == 422
    assert _answer(rid, "checkbox", ["a", "nope"]).status_code == 422
    assert _answer(rid, "checkbox", []).status_code == 201  # empty subset is a valid answer

    rid = _rid("approve_deny", [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}])
    assert _answer(rid, "approve_deny", "yes").status_code == 422
    assert _answer(rid, "approve_deny", "deny").status_code == 201

    rid = _rid("chat", [])
    assert _answer(rid, "chat", "   ").status_code == 422
    assert _answer(rid, "chat", "x" * 4001).status_code == 422
    assert _answer(rid, "chat", "sha256:abc").status_code == 201

    rid = _rid("confirm_rows", [{"id": "r1", "label": "Row 1"}, {"id": "r2", "label": "Row 2"}])
    assert _answer(rid, "confirm_rows", []).status_code == 422
    assert _answer(rid, "confirm_rows", ["r9"]).status_code == 422
    r = _answer(rid, "confirm_rows", ["r2", "r1"])
    assert r.status_code == 201 and r.json()["value"] == ["r2", "r1"]


# --- one terminal transition wins, deterministic 409 -------------------------

def _assert_conflict(resp, rid, state):
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == {"error": "request_terminal", "request_id": rid, "state": state}


def test_terminal_matrix_is_deterministic():
    # answer then answer
    rid = _rid()
    assert _answer(rid, "radio", "a").status_code == 201
    _assert_conflict(_answer(rid, "radio", "b"), rid, "answered")
    # answer then cancel
    _assert_conflict(client.post(f"/v1/requests/{rid}/cancel", json={"reason": "late"}, headers=agent_auth(CALLER)), rid, "answered")
    # cancel then answer, cancel then cancel
    rid = _rid()
    assert client.post(f"/v1/requests/{rid}/cancel", json={"reason": "x"}, headers=agent_auth(CALLER)).status_code == 200
    _assert_conflict(_answer(rid, "radio", "a"), rid, "cancelled")
    _assert_conflict(client.post(f"/v1/requests/{rid}/cancel", json={"reason": "y"}, headers=agent_auth(CALLER)), rid, "cancelled")
    # expiry then answer
    rid = _rid(deadline_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    backdate(rid)
    with SessionLocal() as db:
        results = run_expiry_sweep(db)
    assert [r["action"] for r in results if r["request_id"] == rid] == ["auto_denied"]
    _assert_conflict(_answer(rid, "radio", "a"), rid, "expired")
    # answer then expiry sweep: the sweep skips the answered ask
    rid = _rid(deadline_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    assert _answer(rid, "radio", "a").status_code == 201
    backdate(rid)
    with SessionLocal() as db:
        results = run_expiry_sweep(db)
    assert all(r["request_id"] != rid for r in results)
    assert _state(rid) == "answered"
    with SessionLocal() as db:
        assert db.query(models.AuditRow).filter(models.AuditRow.ask_id == rid).count() == 1


# --- idempotency keys ----------------------------------------------------------

def test_create_retry_with_same_key_returns_the_same_request():
    key = {"Idempotency-Key": f"create-{uuid.uuid4()}"}
    first = _create(headers=key)
    second = _create(headers=key)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json() == second.json()
    assert second.headers.get("Idempotent-Replayed") == "true"
    with SessionLocal() as db:
        n = db.query(models.Ask).filter(models.Ask.id == first.json()["request_id"]).count()
        assert n == 1
    # same key, different body: refused, no third ask
    third = client.post("/v1/requests", json=_body(title="changed"), headers={**agent_auth(CALLER), **key})
    assert third.status_code == 422
    # another caller may reuse the same key text
    other = client.post("/v1/requests", json=_body() | {"caller": "sm-other"}, headers={**agent_auth("sm-other"), **key})
    assert other.status_code == 201 and other.json()["request_id"] != first.json()["request_id"]


def test_answer_retry_with_same_key_replays_without_a_second_decision():
    rid = _rid()
    key = {"Idempotency-Key": f"answer-{uuid.uuid4()}"}
    first = _answer(rid, "radio", "a", headers=key)
    second = _answer(rid, "radio", "a", headers=key)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json() == second.json()
    # same key, other value: 422, not a replay and not a second decision
    assert _answer(rid, "radio", "b", headers=key).status_code == 422
    # without the key the duplicate is the deterministic 409
    _assert_conflict(_answer(rid, "radio", "a"), rid, "answered")
    with SessionLocal() as db:
        assert db.query(models.AuditRow).filter(models.AuditRow.ask_id == rid).count() == 1


def test_cancel_retry_with_same_key_replays():
    rid = _rid()
    key = {**agent_auth(CALLER), "Idempotency-Key": f"cancel-{uuid.uuid4()}"}
    first = client.post(f"/v1/requests/{rid}/cancel", json={"reason": "x"}, headers=key)
    second = client.post(f"/v1/requests/{rid}/cancel", json={"reason": "x"}, headers=key)
    assert first.status_code == 200 and second.status_code == 200 and first.json() == second.json()
    assert client.post(f"/v1/requests/{rid}/cancel", json={"reason": "other"}, headers=key).status_code == 422
    assert client.post("/v1/requests", json=_body(), headers={**agent_auth(CALLER), "Idempotency-Key": "x" * 201}).status_code == 422


def test_idempotency_rows_are_swept_by_ttl():
    from app import idempotency
    key = {"Idempotency-Key": f"ttl-{uuid.uuid4()}"}
    _create(headers=key)
    with SessionLocal() as db:
        n = idempotency.sweep(db, now=datetime.now(timezone.utc) + timedelta(seconds=idempotency.TTL_SECONDS + 5))
    assert n >= 1
    # the key is new again: a retry now creates a second ask
    assert _create(headers=key).headers.get("Idempotent-Replayed") is None


# --- a real race on Postgres --------------------------------------------------

@pytest.mark.skipif(not POSTGRES, reason="SELECT FOR UPDATE race needs Postgres")
def test_concurrent_answers_and_cancel_leave_exactly_one_decision():
    rid = _rid()
    results = []
    barrier = threading.Barrier(5)

    def answer(v):
        barrier.wait()
        results.append(("answer", _answer(rid, "radio", v).status_code))

    def cancel():
        barrier.wait()
        results.append(("cancel", client.post(f"/v1/requests/{rid}/cancel", json={"reason": "race"}, headers=agent_auth(CALLER)).status_code))

    threads = [threading.Thread(target=answer, args=("a",)), threading.Thread(target=answer, args=("b",)),
               threading.Thread(target=answer, args=("a",)), threading.Thread(target=cancel), threading.Thread(target=cancel)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    codes = sorted(c for _, c in results)
    assert codes.count(409) == 4 and len(codes) == 5, results
    assert (codes.count(201) + codes.count(200)) == 1
    with SessionLocal() as db:
        assert db.query(models.AuditRow).filter(models.AuditRow.ask_id == rid).count() == 1
        assert db.get(models.Ask, rid).state in ("answered", "cancelled")
