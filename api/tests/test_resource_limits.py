"""Bounds and shared-budget proofs using synthetic, disposable records."""
import asyncio
import json
import threading
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import events, models, requests_api, limits
from app.db import SessionLocal, engine
from app.limits import consume
from app.main import app
from app.middleware import RequestBounds
from authhelper import ADMIN_AUTH, agent_auth
from deviceauth import human_get, human_post

client = TestClient(app)


def fixture_request(**extra):
    caller = "bounds-" + uuid.uuid4().hex[:12]
    auth = agent_auth(caller)
    body = {"caller": caller, "audience": "owner", "title": "Synthetic bounded request", "control": "chat",
            "options_or_fields": [], "suggested": {}, "deliver_to": {"mode": "poll"}}
    body.update(extra)
    return auth, body


def create(auth, body, headers=None):
    response = client.post("/v1/requests", json=body, headers={**auth, **(headers or {})})
    assert response.status_code == 201, response.text
    return response.json()["request_id"]


def test_body_limit_precedes_auth_and_adds_correlation_id():
    response = client.post("/v1/requests", content=b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413
    assert len(response.headers["x-request-id"]) == 32


def test_chunked_body_limit_and_envelope_coalescing():
    async def scenario(parts, expected_status):
        messages = iter(parts)
        output, received = [], []
        async def receive():
            return next(messages)
        async def send(message):
            output.append(message)
        async def target(scope, bounded_receive, wrapped_send):
            received.append(await bounded_receive())
            await wrapped_send({"type": "http.response.start", "status": 204, "headers": []})
            await wrapped_send({"type": "http.response.body", "body": b""})
        await RequestBounds(target)({"type": "http", "method": "POST"}, receive, send)
        assert output[0]["status"] == expected_status
        return received
    parts = [{"type": "http.request", "body": b"", "more_body": True}] * 1000
    parts.append({"type": "http.request", "body": b"x", "more_body": False})
    assert len(asyncio.run(scenario(parts, 204))) == 1
    oversized = [{"type": "http.request", "body": b"x" * (1024 * 1024), "more_body": True},
                 {"type": "http.request", "body": b"x", "more_body": False}]
    assert asyncio.run(scenario(oversized, 413)) == []


@pytest.mark.parametrize("value", [{"x": "a" * 4001}, {"x": list(range(10001))}, {"x": float("nan")}])
def test_nested_bounds_reject_before_persistence(value):
    auth, body = fixture_request(zoom=value)
    response = client.post("/v1/requests", content=json.dumps(body),
                           headers={**auth, "Content-Type": "application/json"})
    assert response.status_code == 422
    with SessionLocal() as db:
        assert not db.query(models.Ask).filter_by(caller=body["caller"]).count()


def test_complexity_depth_limit():
    nested = {}
    for _ in range(14):
        nested = {"x": nested}
    auth, body = fixture_request(zoom=nested)
    assert client.post("/v1/requests", json=body, headers=auth).status_code == 422


def test_maximum_confirm_rows_remains_supported():
    auth, body = fixture_request(control="confirm_rows",
        options_or_fields=[{"id": str(n), "label": f"Synthetic row {n}"} for n in range(500)])
    rid = create(auth, body)
    response = human_post(client, f"/v1/requests/{rid}/answer", json={"control": "confirm_rows",
        "value": [str(n) for n in range(500)], "surface": "browser_overlay"})
    assert response.status_code == 201, response.text
    assert len(response.json()["value"]) == 500
    body["options_or_fields"].append({"id": "overflow", "label": "overflow"})
    assert client.post("/v1/requests", json=body, headers=auth).status_code == 422


def test_shared_rate_limit_is_atomic_across_sessions():
    identity = uuid.uuid4().hex
    barrier = threading.Barrier(2)
    outcomes = []
    def run():
        with SessionLocal() as db:
            barrier.wait(5)
            try:
                consume(db, identity, 1, now=101)
                db.commit()
                outcomes.append(200)
            except HTTPException as exc:
                outcomes.append(exc.status_code)
                assert exc.headers["Retry-After"] == "19"
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert sorted(outcomes) == [200, 429]
    with SessionLocal() as db:
        consume(db, identity, 1, now=120)
        db.commit()


def test_agent_budget_returns_retry_after_and_correlation(monkeypatch):
    auth, body = fixture_request()
    monkeypatch.setattr(limits, "time", SimpleNamespace(time=lambda: 100))
    monkeypatch.setenv("HCA_ACTOR_RATE_PER_MINUTE", "1")
    assert client.get("/v1/webhooks", headers=auth).status_code == 200
    limited = client.get("/v1/webhooks", headers=auth)
    assert limited.status_code == 429 and 1 <= int(limited.headers["retry-after"]) <= 60
    assert limited.headers["x-request-id"]


def test_quota_rejects_new_write_but_replays_existing_receipt(monkeypatch):
    auth, body = fixture_request()
    with SessionLocal() as db:
        active = db.query(models.Ask).filter(models.Ask.tenant_id == "default",
            models.Ask.state.notin_(requests_api.TERMINAL_STATES)).count()
    monkeypatch.setattr(requests_api, "MAX_ACTIVE_REQUESTS", active + 1)
    key = {"Idempotency-Key": uuid.uuid4().hex}
    rid = create(auth, body, key)
    replay = client.post("/v1/requests", json=body, headers={**auth, **key})
    assert replay.status_code == 201 and replay.json()["request_id"] == rid
    rejected = client.post("/v1/requests", json=body, headers=auth)
    assert rejected.status_code == 429 and rejected.headers["Retry-After"] == "60"


def test_inbox_and_audit_pagination_have_no_duplicate_records():
    auth, body = fixture_request()
    ids = {create(auth, body) for _ in range(3)}
    pending, after = [], ""
    while True:
        response = human_get(client, "/v1/inbox", params={"limit": 1, "after": after})
        assert response.status_code == 200
        assert len(response.json()) <= 1
        pending.extend(r["request_id"] for r in response.json())
        after = response.headers.get("X-HCA-Next-Cursor", "")
        if not after:
            break
    assert ids.issubset(set(pending)) and len(pending) == len(set(pending))
    for rid in ids:
        assert human_post(client, f"/v1/requests/{rid}/answer", json={"control": "chat", "value": "Synthetic",
            "surface": "browser_overlay"}).status_code == 201
    answered, after = [], ""
    while True:
        response = human_get(client, "/v1/audit", params={"agent": body["caller"], "limit": 1, "after": after})
        assert response.status_code == 200
        assert len(response.json()) <= 1
        answered.extend(r["request_id"] for r in response.json())
        after = response.headers.get("X-HCA-Next-Cursor", "")
        if not after:
            break
    assert set(answered) == ids and len(answered) == len(ids)
    assert human_get(client, "/v1/inbox", params={"limit": 501}).status_code == 422


@pytest.mark.parametrize("path,field", [("/v1/tenants", "tenant_id"), ("/v1/devices", "device_id"), ("/v1/api-keys", "prefix")])
def test_admin_reads_follow_bounded_cursors(path, field):
    # Earlier tests seed multiple keys and devices. Add tenants and devices
    # directly as synthetic fixtures; route authorization is still exercised.
    from datetime import datetime, timezone
    suffix = uuid.uuid4().hex
    with SessionLocal() as db:
        for n in range(3):
            db.add(models.Tenant(id=f"pagination-{suffix}-{n}", slug=f"pagination-{suffix}-{n}",
                                 name="Synthetic", created_at=datetime.now(timezone.utc)))
            db.add(models.Device(id=f"pagination-device-{suffix}-{n}", label="Synthetic", tenant_id="default",
                surface="browser_overlay", public_key_pem="unused", created_at=datetime.now(timezone.utc)))
        db.commit()
    full = client.get(path, headers=ADMIN_AUTH)
    expected = {record[field] for record in full.json()}
    found, after = [], ""
    while True:
        response = client.get(path, params={"limit": 1, "after": after}, headers=ADMIN_AUTH)
        assert response.status_code == 200 and len(response.json()) <= 1
        found.extend(record[field] for record in response.json())
        after = response.headers.get("X-HCA-Next-Cursor", "")
        if not after:
            break
    assert set(found) == expected and len(found) == len(expected)
    assert client.get(path, params={"limit": 501}, headers=ADMIN_AUTH).status_code == 422


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="tenant/event lock compatibility requires PostgreSQL")
def test_quota_lock_remains_compatible_with_event_foreign_key_lock():
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
            assert completed.wait(2), "event foreign-key check blocked behind quota lock"
        finally:
            db.rollback()
            thread.join(5)
    assert not errors and not thread.is_alive()


def test_contract_describes_limits_cursors_and_stream_content():
    spec = app.openapi()
    inbox = spec["paths"]["/v1/inbox"]["get"]["responses"]
    assert {"200", "413", "429"}.issubset(inbox)
    assert "X-HCA-Next-Cursor" in inbox["200"]["headers"]
    assert "X-Request-ID" in inbox["413"]["headers"]
    assert "Retry-After" in inbox["429"]["headers"]
    assert "text/event-stream" in spec["paths"]["/v1/inbox/stream"]["get"]["responses"]["200"]["content"]
    assert spec["components"]["schemas"]["PurgeResult"]["properties"]["purged"]["minimum"] == 0


@pytest.mark.parametrize("resource", ["device", "owner", "key"])
def test_concurrent_registration_respects_tenant_quota(monkeypatch, resource):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from app import apikeys, devices_api, tenancy
    tenant_id = "quota-" + uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(models.Tenant(id=tenant_id, slug=tenant_id, name="Synthetic", created_at=datetime.now(timezone.utc)))
        db.commit()
    if resource == "device":
        monkeypatch.setattr(devices_api, "MAX_ACTIVE_DEVICES", 1)
        path = "/v1/devices"
        bodies = []
        for n in range(2):
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            bodies.append({"label": "Synthetic", "public_key_pem": pem, "tenant_id": tenant_id})
        model = models.Device
    elif resource == "owner":
        monkeypatch.setattr(tenancy, "MAX_ACTIVE_OWNERS", 1)
        path = f"/v1/tenants/{tenant_id}/owners"
        bodies = [{"email": f"synthetic{n}@example.test"} for n in range(2)]
        model = models.TenantOwner
    else:
        monkeypatch.setattr(apikeys, "MAX_ACTIVE_API_KEYS", 1)
        path = "/v1/api-keys"
        bodies = [{"caller": tenant_id + str(n), "tenant_id": tenant_id} for n in range(2)]
        model = models.ApiKey
    barrier = threading.Barrier(2)
    def register(body):
        barrier.wait(5)
        return client.post(path, json=body, headers=ADMIN_AUTH)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, bodies))
    assert sorted(result.status_code for result in results) == [201, 429], [result.text for result in results]
    winner = bodies[[result.status_code for result in results].index(201)]
    # Repeating an existing registration/rotating an existing key uses no
    # additional slot and remains possible at the quota.
    assert client.post(path, json=winner, headers=ADMIN_AUTH).status_code == 201
    with SessionLocal() as db:
        assert db.query(model).filter_by(tenant_id=tenant_id).count() == 1
