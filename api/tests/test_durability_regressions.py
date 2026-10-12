"""Security/correctness regressions; PostgreSQL-only cases are marked explicitly.

Run this module against its own DATABASE_URL. Network responses and DNS are
controlled here; no live receiver or credentials are required.
"""
import asyncio
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, delete

from app import events, idempotency, jobs, models, requests_api, webhooks
from app.db import SessionLocal, engine
from app.expiry import expire_ask
from app.main import app
from authhelper import agent_auth
from deviceauth import DEVICE_ID, ensure_registered, human_post
from overdue import backdate

client = TestClient(app)


def _fixture_request(**extra):
    caller = "durability-" + uuid.uuid4().hex[:12]
    auth = agent_auth(caller)
    body = {"caller": caller, "audience": "owner", "title": "durability " + uuid.uuid4().hex,
            "control": "radio", "options_or_fields": [{"id": "a", "label": "A"}],
            "suggested": {"option_id": "a", "why": "test"}, "deliver_to": {"mode": "poll"}}
    body.update(extra)
    return auth, body


def _create(auth, body, headers=None):
    r = client.post("/v1/requests", json=body, headers={**auth, **(headers or {})})
    assert r.status_code == 201, r.text
    return r.json()["request_id"]


def _answer(rid, headers=None, **extra):
    return human_post(client, f"/v1/requests/{rid}/answer", json={"control": "radio", "value": "a",
                      "surface": "browser_overlay", **extra}, headers=headers or {})


@pytest.mark.parametrize("operation", ["answer", "cancel"])
def test_idempotency_key_cannot_replay_another_resource(operation):
    auth, body = _fixture_request()
    r1, r2 = _create(auth, body), _create(auth, body)
    key = {"Idempotency-Key": uuid.uuid4().hex}
    if operation == "answer":
        first, second = _answer(r1, key), _answer(r2, key)
    else:
        first = client.post(f"/v1/requests/{r1}/cancel", json={"reason": "x"}, headers={**auth, **key})
        second = client.post(f"/v1/requests/{r2}/cancel", json={"reason": "x"}, headers={**auth, **key})
    assert first.status_code in (200, 201)
    assert second.status_code == 422
    with SessionLocal() as db:
        assert db.get(models.Ask, r2).state == "queued"


def test_create_store_failure_rolls_back_ask_jobs_events_and_key(monkeypatch):
    auth, body = _fixture_request()
    key = {"Idempotency-Key": uuid.uuid4().hex}
    with monkeypatch.context() as patch:
        patch.setattr(idempotency, "store", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash before commit")))
        with pytest.raises(RuntimeError, match="crash before commit"):
            client.post("/v1/requests", json=body, headers={**auth, **key})
    with SessionLocal() as db:
        assert db.query(models.Ask).filter_by(title=body["title"]).count() == 0
        assert db.query(models.IdempotencyKey).filter_by(key=key["Idempotency-Key"]).count() == 0
        assert not any(row.data.get("title") == body["title"] for row in db.query(models.Event))
    rid = _create(auth, body, key)
    replay = client.post("/v1/requests", json=body, headers={**auth, **key})
    assert replay.status_code == 201 and replay.json()["request_id"] == rid


@pytest.mark.parametrize("operation", ["create", "answer", "cancel", "expire"])
def test_event_write_failure_rolls_back_business_effects(monkeypatch, operation):
    auth, body = _fixture_request(deliver_to={"mode": "webhook", "url": "https://example.test/hook"})
    rid = None if operation == "create" else _create(auth, body)
    if operation == "expire":
        backdate(rid)
    with monkeypatch.context() as patch:
        patch.setattr(events, "enqueue", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("event unavailable")))
        with pytest.raises(RuntimeError, match="event unavailable"):
            if operation == "create":
                client.post("/v1/requests", json=body, headers=auth)
            elif operation == "answer":
                _answer(rid)
            elif operation == "cancel":
                client.post(f"/v1/requests/{rid}/cancel", json={"reason": "x"}, headers=auth)
            else:
                with SessionLocal() as db:
                    expire_ask(db, rid)
    with SessionLocal() as db:
        if operation == "create":
            assert db.query(models.Ask).filter_by(title=body["title"]).count() == 0
        else:
            assert db.get(models.Ask, rid).state == "queued"
            assert db.query(models.AuditRow).filter_by(ask_id=rid).count() == 0
            assert db.query(models.WebhookDelivery).filter_by(ask_id=rid).count() == 0


def test_simultaneous_create_retries_share_one_committed_receipt():
    auth, body = _fixture_request()
    key = {"Idempotency-Key": uuid.uuid4().hex}
    barrier = threading.Barrier(4)
    responses, errors = [], []
    def create():
        try:
            barrier.wait(timeout=5)
            responses.append(client.post("/v1/requests", json=body, headers={**auth, **key}))
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=create) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not errors and all(not t.is_alive() for t in threads), errors
    assert len(responses) == 4 and {r.status_code for r in responses} == {201}
    assert len({r.json()["request_id"] for r in responses}) == 1
    with SessionLocal() as db:
        assert db.query(models.Ask).filter_by(title=body["title"]).count() == 1
        assert db.query(models.IdempotencyKey).filter_by(key=key["Idempotency-Key"]).count() == 1


@pytest.mark.parametrize("policy,answer_exists", [("auto_deny", True), ("expire", False)])
def test_overdue_answers_apply_policy_without_waiting_for_worker(policy, answer_exists):
    auth, body = _fixture_request(expiry_policy=policy)
    rid = _create(auth, body)
    backdate(rid)
    key = {"Idempotency-Key": uuid.uuid4().hex}
    response = _answer(rid, key)
    assert response.status_code == 409 and response.json()["detail"]["state"] == "expired"
    with SessionLocal() as db:
        ask = db.get(models.Ask, rid)
        assert ask.state == "expired" and bool(ask.answer) is answer_exists
        if ask.answer:
            assert ask.answer.value["decision"] == "deny"
        assert db.query(models.IdempotencyKey).filter_by(key=key["Idempotency-Key"]).count() == 0
        assert db.query(models.Event).filter_by(event="request.expired").filter(
            models.Event.tenant_id == ask.tenant_id).count() >= 1


def test_hold_remains_answerable_and_does_not_create_repeated_expiry_jobs():
    auth, body = _fixture_request(expiry_policy="hold")
    rid = _create(auth, body)
    backdate(rid)
    with SessionLocal() as db:
        job = db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id == rid,
                          models.ScheduledJob.kind == jobs.KIND_EXPIRE)).scalar_one()
        job.run_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    jobs.run_due(owner="hold-test")
    with SessionLocal() as db:
        for _ in range(3):
            jobs.reconcile(db)
        assert db.query(models.ScheduledJob).filter_by(ask_id=rid, kind=jobs.KIND_EXPIRE).count() == 1
    assert _answer(rid).status_code == 201


def test_audit_preserves_verified_identity_and_isolates_client_impersonation():
    ensure_registered()
    # Device labels are descriptive; the stable authenticated device ID is
    # always snapshotted even when development mode has no Google identity.
    auth, body = _fixture_request()
    rid = _create(auth, body)
    r = _answer(rid, answered_by="CEO", answered_role="administrator", surface="macos_app",
                passthrough={"verified_identity": {"subject": "forged"}})
    assert r.status_code == 201, r.text
    record = r.json()
    assert record["answered_by"] != "CEO"
    assert record["answered_role"] in ("owner", "device")
    assert record["surface"] == "browser_overlay"
    assert record["passthrough"]["verified_identity"]["device_id"] == DEVICE_ID
    assert record["passthrough"]["client_metadata"]["answered_role"] == "administrator"


def _due_job():
    auth, body = _fixture_request()
    rid = _create(auth, body)
    backdate(rid)
    with SessionLocal() as db:
        job = db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id == rid,
                          models.ScheduledJob.kind == jobs.KIND_EXPIRE)).scalar_one()
        job.run_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
        jid = job.id
        db.commit()
    return rid, jid


@pytest.mark.parametrize("fails", [False, True])
def test_stale_worker_cannot_complete_or_release_a_new_generation(monkeypatch, fails):
    _, jid = _due_job()
    with SessionLocal() as db:
        claimed = jobs.claim_due(db, "old", job_ids=[jid])
    def takeover(*args):
        with SessionLocal() as db:
            db.execute(update(models.ScheduledJob).where(models.ScheduledJob.id == jid).values(
                lease_owner="new", attempts=models.ScheduledJob.attempts + 1,
                lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1)))
            db.commit()
        if fails:
            raise RuntimeError("old worker error")
        return {"action": "stale"}
    monkeypatch.setattr(jobs, "_run_one", takeover)
    assert jobs.run_claimed(claimed) == []
    with SessionLocal() as db:
        row = db.get(models.ScheduledJob, jid)
        assert row.lease_owner == "new" and row.attempts == 2
        assert row.done_at is None and row.last_error is None


def test_long_execution_renews_lease_before_a_competing_claim(monkeypatch):
    _, jid = _due_job()
    monkeypatch.setattr(jobs, "LEASE_SECONDS", 0.18)
    real, entered = jobs._run_one, threading.Event()
    executions, errors = [], []
    def slow(*args):
        if args[0] == jid:
            executions.append(jid)
            entered.set()
            time.sleep(0.4)
        return real(*args)
    monkeypatch.setattr(jobs, "_run_one", slow)
    def worker():
        try:
            with SessionLocal() as db:
                claimed = jobs.claim_due(db, "renewing", job_ids=[jid])
            jobs.run_claimed(claimed)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(3)
    time.sleep(0.25)  # longer than the initial lease
    with SessionLocal() as db:
        assert jobs.claim_due(db, "competitor", job_ids=[jid]) == []
    thread.join(5)
    assert not thread.is_alive() and not errors
    assert executions == [jid]
    with SessionLocal() as db:
        assert db.get(models.ScheduledJob, jid).done_at is not None


def test_next_batch_job_is_unleased_until_it_is_ready_to_execute(monkeypatch):
    _, first = _due_job()
    _, second = _due_job()
    real = jobs._run_one
    observed = []
    def inspect(*args):
        if args[0] == first:
            with SessionLocal() as db:
                observed.append(db.get(models.ScheduledJob, second).lease_owner)
        return real(*args)
    monkeypatch.setattr(jobs, "_run_one", inspect)
    jobs.run_due(owner="serial")
    assert observed == [None]


def test_webhook_deadline_bounds_delayed_response_headers(monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.4)
        return httpx.Response(200)
    webhooks.set_transport_factory(lambda: httpx.MockTransport(slow))
    monkeypatch.setattr(webhooks, "TOTAL_TIMEOUT_SECONDS", 0.04)
    started = time.monotonic()
    status, error = webhooks._post("https://example.test/hook", b"{}", {})
    assert status is None and "deadline" in error
    assert time.monotonic() - started < 0.25


def test_webhook_never_consumes_slow_or_compressed_response_bodies():
    class NeverRead(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("recipient body must never be consumed/decompressed")
            yield b""
    webhooks.set_transport_factory(lambda: httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=NeverRead())))
    assert webhooks._post("https://example.test/hook", b"{}", {}) == (200, None)


def test_webhook_deadline_includes_hanging_dns_and_lookup_capacity_is_bounded(monkeypatch):
    release, entered = threading.Event(), threading.Event()
    original = webhooks.ssrf.check_url
    def slow(url):
        entered.set()
        assert release.wait(2)
        return original(url)
    monkeypatch.setattr(webhooks.ssrf, "check_url", slow)
    monkeypatch.setattr(webhooks, "TOTAL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(webhooks, "_DNS_SLOTS", threading.BoundedSemaphore(1))
    try:
        started = time.monotonic()
        assert "deadline" in webhooks._post("https://example.test/hook", b"{}", {})[1]
        assert entered.is_set() and time.monotonic() - started < 0.25
        assert "capacity" in webhooks._post("https://example.test/hook", b"{}", {})[1]
    finally:
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if webhooks._DNS_SLOTS.acquire(blocking=False):
                webhooks._DNS_SLOTS.release()
                break
            time.sleep(0.01)


def test_webhook_send_releases_database_connection_and_authenticates_dedupe_id(monkeypatch):
    auth, body = _fixture_request(deliver_to={"mode": "webhook", "url": "https://example.test/hook"})
    rid = _create(auth, body)
    assert _answer(rid).status_code == 201
    observed = []
    def send(url, payload, headers):
        observed.append(engine.pool.checkedout())
        import json
        assert json.loads(payload)["delivery_id"] == headers["X-HCA-Delivery-Id"]
        # The reference receiver requires both signature validity and the
        # authenticated ID/header agreement before durable deduplication.
        signature = webhooks.signature_header(["test-secret"], 1000, payload)
        assert webhooks.verify_signature("test-secret", signature, payload,
                                         delivery_id=headers["X-HCA-Delivery-Id"], now=1000)
        assert not webhooks.verify_signature("test-secret", signature, payload,
                                             delivery_id="substituted", now=1000)
        assert not webhooks.verify_signature("test-secret", signature, payload, now=1000)
        return 200, None
    monkeypatch.setattr(webhooks, "_post", send)
    with SessionLocal() as db:
        delivery = db.execute(select(models.WebhookDelivery).where(models.WebhookDelivery.ask_id == rid)).scalar_one()
        did = delivery.id
    assert webhooks.attempt_delivery(did, SessionLocal)["action"] == "delivered"
    assert observed == [0]


def test_active_request_quota_rejects_new_ask_but_still_replays_existing_key(monkeypatch):
    auth, body = _fixture_request()
    with SessionLocal() as db:
        active = db.query(models.Ask).filter(models.Ask.tenant_id == "default",
                       models.Ask.state.notin_(requests_api.TERMINAL_STATES)).count()
    monkeypatch.setattr(requests_api, "MAX_ACTIVE_REQUESTS", active + 1)
    key = {"Idempotency-Key": uuid.uuid4().hex}
    rid = _create(auth, body, key)
    replay = client.post("/v1/requests", json=body, headers={**auth, **key})
    assert replay.status_code == 201 and replay.json()["request_id"] == rid
    full = client.post("/v1/requests", json=body, headers=auth)
    assert full.status_code == 429 and full.headers["Retry-After"] == "60"


def test_replay_reports_aged_out_truncated_and_future_cursors():
    tenant = "default"
    now = datetime.now(timezone.utc)
    env = events.publish("request.expired", {"request_id": "gap"}, tenant_id=tenant)
    with SessionLocal() as db:
        db.get(models.Event, env["id"]).created_at = now.replace(tzinfo=None) - timedelta(days=2)
        db.commit()
        assert events.replay_gap(db, tenant_id=tenant, device_id=DEVICE_ID, after_id=env["id"] - 1,
                                 now=now) == "replay_expired"
        assert events.replay_gap(db, tenant_id=tenant, device_id=DEVICE_ID, after_id=2**63 - 1) == "cursor_ahead"
    for _ in range(2):
        events.publish("request.expired", {}, tenant_id=tenant)
    with SessionLocal() as db:
        assert events.replay_gap(db, tenant_id=tenant, device_id=DEVICE_ID, after_id=env["id"], max_events=1) == "replay_limit"


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="commit-order advisory lock proof requires PostgreSQL")
def test_delayed_event_commit_cannot_be_overtaken_by_a_higher_cursor():
    first, second_started, second_allocated = threading.Event(), threading.Event(), threading.Event()
    release = threading.Event()
    result, errors = {}, []
    def writer_one():
        try:
            with SessionLocal() as db:
                result["first"] = events.enqueue(db, "request.cancelled", {"request_id": "delayed"}, tenant_id="default")["id"]
                first.set()
                assert release.wait(5)
                db.commit()
        except Exception as exc:
            errors.append(exc)
    def writer_two():
        try:
            assert first.wait(5)
            second_started.set()
            with SessionLocal() as db:
                result["second"] = events.enqueue(db, "request.cancelled", {"request_id": "later"}, tenant_id="default")["id"]
                second_allocated.set()
                db.commit()
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=writer_one), threading.Thread(target=writer_two)]
    for thread in threads:
        thread.start()
    try:
        assert second_started.wait(5)
        assert not second_allocated.wait(0.1), "higher event ID was allocated before the lower ID committed"
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
    assert not errors and all(not t.is_alive() for t in threads), errors
    assert result["first"] < result["second"]
    with SessionLocal() as db:
        replayed = events.replay(db, tenant_id="default", device_id=DEVICE_ID, after_id=result["first"] - 1)
    assert [e["id"] for e in replayed] == [result["first"], result["second"]]


@pytest.mark.parametrize("operation", ["create", "answer", "cancel"])
def test_lost_response_after_commit_replays_every_committed_effect(monkeypatch, operation):
    auth, body = _fixture_request()
    rid = None if operation == "create" else _create(auth, body)
    key = {"Idempotency-Key": uuid.uuid4().hex}
    def send():
        if operation == "create":
            return client.post("/v1/requests", json=body, headers={**auth, **key})
        if operation == "answer":
            return _answer(rid, key)
        return client.post(f"/v1/requests/{rid}/cancel", json={"reason": "x"}, headers={**auth, **key})
    with monkeypatch.context() as patch:
        patch.setattr(events, "wake_all", lambda: (_ for _ in ()).throw(RuntimeError("response lost after commit")))
        with pytest.raises(RuntimeError, match="response lost after commit"):
            send()
    replayed = send()
    assert replayed.status_code in (200, 201)
    assert replayed.headers["Idempotent-Replayed"] == "true"
    rid = replayed.json()["request_id"]
    with SessionLocal() as db:
        assert db.query(models.Ask).filter_by(title=body["title"]).count() == 1
        matching = [row for row in db.query(models.Event) if row.data.get("request_id") == rid]
        assert len(matching) == (1 if operation == "create" else 2)
        assert db.query(models.AuditRow).filter_by(ask_id=rid).count() == (0 if operation == "create" else 1)


def test_answer_at_exact_deadline_applies_expiry_policy(monkeypatch):
    auth, body = _fixture_request()
    rid = _create(auth, body)
    boundary = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.get(models.Ask, rid).expires_at = boundary.replace(tzinfo=None)
        db.commit()
    class FixedClock:
        @staticmethod
        def now(tz):
            return boundary
    monkeypatch.setattr(requests_api, "datetime", FixedClock)
    assert _answer(rid).status_code == 409
    with SessionLocal() as db:
        assert db.get(models.Ask, rid).state == "expired"


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="foreign-key row-lock compatibility requires PostgreSQL")
def test_quota_lock_allows_event_foreign_key_checks_to_finish():
    # If quota used FOR UPDATE, the event writer would block while holding
    # the advisory lock. A quota writer then requesting that lock deadlocks.
    completed, errors = threading.Event(), []
    def publish():
        try:
            events.publish("request.cancelled", {}, tenant_id="default")
            completed.set()
        except Exception as exc:
            errors.append(exc)
    with SessionLocal() as db:
        requests_api._lock_tenant_for_quota(db, "default")
        thread = threading.Thread(target=publish)
        thread.start()
        try:
            assert completed.wait(2), "event FK check blocked behind the tenant quota lock"
        finally:
            db.rollback()
            thread.join(5)
    assert not errors and not thread.is_alive(), errors


def test_event_cursor_remains_monotonic_after_retention_empties_the_log():
    before = events.publish("request.cancelled", {"request_id": "before-prune"}, tenant_id="default")
    with SessionLocal() as db:
        db.execute(delete(models.Event))
        db.commit()
    after = events.publish("request.cancelled", {"request_id": "after-prune"}, tenant_id="default")
    assert after["id"] > before["id"]
    with SessionLocal() as db:
        assert [e["id"] for e in events.replay(db, tenant_id="default", device_id=DEVICE_ID,
                                               after_id=before["id"])] == [after["id"]]
