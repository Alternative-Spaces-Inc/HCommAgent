"""HCA-P21a Day 2: outcomes reach the registered destination through the
durable outbox (webhook_deliveries plus scheduled_jobs kind webhook), signed
and pinned. Deliveries run when the job runner runs, so every test calls
jobs.run_due() after the state change."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

from overdue import backdate

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["WEBHOOK_RETRY_SCHEDULE"] = "0,0,0,0,0,0,0"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import SessionLocal
from authhelper import agent_auth, DESTINATION_SECRETS, register_destination, TEST_HOOK_URL
from app import jobs, models, webhooks
from app.expiry import run_expiry_sweep
from deviceauth import human_post
from conftest import PUBLIC_TEST_ADDRESS


client = TestClient(app)


def _sample(url=TEST_HOOK_URL, mode="webhook", **extra):
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


def _deliveries(request_id):
    with SessionLocal() as db:
        return db.execute(select(models.WebhookDelivery).where(models.WebhookDelivery.ask_id == request_id)).scalars().all()


def _run_jobs(times=1):
    out = []
    for _ in range(times):
        out.extend(jobs.run_due(owner="test-worker"))
    return out


def _secret_for(caller="loop-agent", url=TEST_HOOK_URL):
    return DESTINATION_SECRETS[register_destination(caller, url)]


def _create_and_answer():
    request_id = client.post("/v1/requests", json=_sample(), headers=agent_auth("loop-agent")).json()["request_id"]
    resp = human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "approve_deny", "value": "approve", "surface": "browser_overlay"})
    assert resp.status_code == 201
    return request_id


def test_answer_enqueues_then_the_runner_posts_a_signed_pinned_request(webhook_recorder):
    request_id = _create_and_answer()
    # Nothing is sent inline; the outbox row and its job exist.
    assert webhook_recorder.requests == []
    [d] = _deliveries(request_id)
    assert d.state == "queued" and d.id.startswith("whdl_")

    results = _run_jobs()
    assert [r["action"] for r in results if r.get("request_id") == request_id] == ["delivered"]

    [req] = [r for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id]
    # Pinned: connected to the checked address, Host and SNI name the registered host.
    assert req.url.host == PUBLIC_TEST_ADDRESS and req.url.path == "/hook"
    assert req.headers["host"] == "example.test"
    assert req.extensions.get("sni_hostname") == "example.test"
    assert req.headers["x-hca-event"] == "request.answered"
    assert req.headers["x-hca-request-id"] == request_id
    assert req.headers["x-hca-delivery-id"] == d.id
    payload = json.loads(req.content)
    assert payload["event"] == "request.answered"
    assert payload["request_id"] == request_id
    assert payload["state"] == "answered"
    assert payload["answer"]["value"] == "approve"
    assert payload["answer"]["surface"] == "browser_overlay"

    # The signature verifies with the documented receiver check under the secret returned once.
    assert webhooks.verify_signature(_secret_for(), req.headers["x-hca-signature"], req.content,
                                     delivery_id=req.headers["x-hca-delivery-id"], now=int(req.headers["x-hca-timestamp"]))
    assert not webhooks.verify_signature("whsec_wrong", req.headers["x-hca-signature"], req.content,
                                         delivery_id=req.headers["x-hca-delivery-id"], now=int(req.headers["x-hca-timestamp"]))
    assert not webhooks.verify_signature(_secret_for(), req.headers["x-hca-signature"], req.content + b" ",
                                         delivery_id=req.headers["x-hca-delivery-id"], now=int(req.headers["x-hca-timestamp"]))
    # A stale timestamp is refused.
    assert not webhooks.verify_signature(_secret_for(), req.headers["x-hca-signature"], req.content,
                                         delivery_id=req.headers["x-hca-delivery-id"], now=int(req.headers["x-hca-timestamp"]) + 301)

    rows = _attempts(request_id)
    assert [(r.attempt, r.status_code, r.ok, r.delivery_id) for r in rows] == [(1, 200, True, d.id)]
    [d] = _deliveries(request_id)
    assert d.state == "delivered" and d.attempts == 1 and d.delivered_at is not None


def test_cancel_posts_request_cancelled(webhook_recorder):
    request_id = client.post("/v1/requests", json=_sample(), headers=agent_auth("loop-agent")).json()["request_id"]
    resp = client.post(f"/v1/requests/{request_id}/cancel", json={"reason": "superseded"}, headers=agent_auth("loop-agent"))
    assert resp.status_code == 200
    _run_jobs()
    matching = [r for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id]
    assert len(matching) == 1
    payload = json.loads(matching[0].content)
    assert payload["event"] == "request.cancelled"
    assert payload["state"] == "cancelled"
    assert payload["answer"]["value"] == {"decision": "cancel", "reason": "superseded"}


def test_auto_deny_expiry_posts_request_expired_with_deny_answer(webhook_recorder):
    soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    request_id = client.post("/v1/requests", json=_sample(deadline_at=soon), headers=agent_auth("loop-agent")).json()["request_id"]
    backdate(request_id)

    with SessionLocal() as db:
        results = run_expiry_sweep(db)
    assert [r["action"] for r in results if r["request_id"] == request_id] == ["auto_denied"]
    _run_jobs()

    matching = [r for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id]
    assert len(matching) == 1
    payload = json.loads(matching[0].content)
    assert payload["event"] == "request.expired"
    assert payload["state"] == "expired"
    assert payload["answer"]["value"]["decision"] == "deny"


def test_plain_expire_posts_request_expired_with_null_answer(webhook_recorder):
    soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    request_id = client.post("/v1/requests", json=_sample(deadline_at=soon, expiry_policy="expire"), headers=agent_auth("loop-agent")).json()["request_id"]
    backdate(request_id)

    with SessionLocal() as db:
        run_expiry_sweep(db)
    _run_jobs()

    matching = [r for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id]
    assert len(matching) == 1
    payload = json.loads(matching[0].content)
    assert payload["event"] == "request.expired"
    assert payload["answer"] is None


def test_failure_reschedules_and_the_retry_survives_a_restart(webhook_recorder):
    """The worker that saw the failure is gone (a fresh run_due stands for
    the restarted instance); the delivery completes from the table, with
    the same delivery id, and every attempt is on record."""
    request_id = _create_and_answer()
    [d] = _deliveries(request_id)

    webhook_recorder.status_code = 503
    results = _run_jobs()
    assert [r["action"] for r in results if r.get("request_id") == request_id] == ["retry_scheduled"]
    [d1] = _deliveries(request_id)
    assert d1.state == "queued" and d1.attempts == 1 and "503" in d1.last_error
    # The retry is a new job row pointing at the same delivery.
    with SessionLocal() as db:
        open_jobs = db.execute(select(models.ScheduledJob).where(
            models.ScheduledJob.ref_id == d.id, models.ScheduledJob.done_at.is_(None))).scalars().all()
    assert len(open_jobs) == 1

    webhook_recorder.status_code = 200
    results = _run_jobs()  # a different "instance" picks the row up
    assert [r["action"] for r in results if r.get("request_id") == request_id] == ["delivered"]
    ids = [r.headers["x-hca-delivery-id"] for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id]
    assert ids == [d.id, d.id]
    rows = _attempts(request_id)
    assert [(r.attempt, r.status_code, r.ok) for r in rows] == [(1, 503, False), (2, 200, True)]
    [d2] = _deliveries(request_id)
    assert d2.state == "delivered" and d2.attempts == 2


def test_eighth_failure_marks_the_delivery_dead_with_an_audit_row(webhook_recorder):
    request_id = _create_and_answer()
    [d] = _deliveries(request_id)
    webhook_recorder.status_code = 500
    actions = []
    for _ in range(9):  # more passes than attempts: the dead row is never retried
        actions += [r["action"] for r in _run_jobs() if r.get("request_id") == request_id]
    assert actions == ["retry_scheduled"] * 7 + ["dead"]
    assert len([r for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id]) == 8
    [d1] = _deliveries(request_id)
    assert d1.state == "dead" and d1.attempts == 8 and d1.dead_at is not None
    with SessionLocal() as db:
        ev = db.execute(select(models.WebhookDestinationEvent).where(
            models.WebhookDestinationEvent.action == "delivery.dead",
            models.WebhookDestinationEvent.destination_id == d.destination_id)).scalars().all()
    assert any(e.detail["delivery_id"] == d.id and e.detail["attempts"] == 8 for e in ev)


def test_connect_error_is_recorded_and_retried(webhook_recorder, monkeypatch):
    import httpx

    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    webhooks.set_transport_factory(lambda: httpx.MockTransport(boom))
    request_id = _create_and_answer()
    results = _run_jobs()
    assert [r["action"] for r in results if r.get("request_id") == request_id] == ["retry_scheduled"]
    rows = _attempts(request_id)
    assert rows[0].status_code is None and rows[0].ok is False and "ConnectError" in rows[0].error


def test_redirect_is_a_failure_not_followed(webhook_recorder):
    import httpx
    seen = []

    def redirect(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.test/"})
    webhooks.set_transport_factory(lambda: httpx.MockTransport(redirect))
    request_id = _create_and_answer()
    results = _run_jobs()
    assert [r["action"] for r in results if r.get("request_id") == request_id] == ["retry_scheduled"]
    # Leftover retries from earlier tests may run in the same pass; count this ask only.
    assert len([r for r in seen if r.headers["x-hca-request-id"] == request_id]) == 1
    assert "302" in _attempts(request_id)[0].error


def test_revoked_destination_fails_dead_at_once(webhook_recorder):
    url = "https://revoke-me.test/hook"
    dest_id = register_destination("loop-agent", url)
    request_id = client.post("/v1/requests", json=_sample(url=url), headers=agent_auth("loop-agent")).json()["request_id"]
    assert client.delete(f"/v1/webhooks/{dest_id}", headers=agent_auth("loop-agent")).status_code == 200
    resp = human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "approve_deny", "value": "approve", "surface": "browser_overlay"})
    assert resp.status_code == 201
    results = _run_jobs()
    assert [r["action"] for r in results if r.get("request_id") == request_id] == ["dead"]
    assert [r for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id] == []
    [d] = _deliveries(request_id)
    assert d.state == "dead" and "revoked" in d.last_error


def test_rotation_overlap_signs_under_both_secrets(webhook_recorder):
    url = "https://rotate-me.test/hook"
    dest_id = register_destination("loop-agent", url)
    old_secret = DESTINATION_SECRETS[dest_id]
    request_id = client.post("/v1/requests", json=_sample(url=url), headers=agent_auth("loop-agent")).json()["request_id"]
    rotated = client.post(f"/v1/webhooks/{dest_id}/rotate", headers=agent_auth("loop-agent")).json()
    new_secret = rotated["secret"]
    resp = human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "approve_deny", "value": "approve", "surface": "browser_overlay"})
    assert resp.status_code == 201
    _run_jobs()
    [req] = [r for r in webhook_recorder.requests if r.headers["x-hca-request-id"] == request_id]
    header = req.headers["x-hca-signature"]
    assert header.count("v1=") == 2
    ts = int(req.headers["x-hca-timestamp"])
    assert webhooks.verify_signature(old_secret, header, req.content, delivery_id=req.headers["x-hca-delivery-id"], now=ts)
    assert webhooks.verify_signature(new_secret, header, req.content, delivery_id=req.headers["x-hca-delivery-id"], now=ts)


def test_poll_mode_is_recorded_as_skipped(webhook_recorder):
    request_id = client.post("/v1/requests", json=_sample(mode="poll", url=None), headers=agent_auth("loop-agent")).json()["request_id"]
    resp = human_post(client, f"/v1/requests/{request_id}/answer", json={"control": "approve_deny", "value": "approve", "surface": "browser_overlay"})
    assert resp.status_code == 201
    _run_jobs()
    assert webhook_recorder.requests == []
    assert _deliveries(request_id) == []
    rows = _attempts(request_id)
    assert len(rows) == 1
    assert rows[0].attempt == 0 and rows[0].ok is False
    assert "skipped" in rows[0].error
