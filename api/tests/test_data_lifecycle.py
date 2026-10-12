"""Tenant export/purge and bounded transient retention; no live credentials."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import events, inbox_api, models, retention, idempotency
from app.apikeys import issue_api_key
from app.db import SessionLocal
from app.limits import RateLimitBucket
from app.main import app
from authhelper import ADMIN_AUTH

client = TestClient(app)


def tenant_fixture():
    suffix = uuid.uuid4().hex
    tenant_id = "data-" + suffix
    caller = "data-agent-" + suffix[:12]
    with SessionLocal() as db:
        db.add(models.Tenant(id=tenant_id, slug=suffix, name="Synthetic", created_at=datetime.now(timezone.utc)))
        db.commit()
        auth = {"Authorization": "Bearer " + issue_api_key(db, caller, tenant_id)["api_key"]}
    body = {"caller": caller, "audience": "owner", "title": "Synthetic", "control": "chat",
            "options_or_fields": [], "suggested": {}, "deliver_to": {"mode": "poll"}}
    return tenant_id, suffix, auth, body


def create(auth, body):
    response = client.post("/v1/requests", json=body, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()["request_id"]


def cancel(auth, rid):
    assert client.post(f"/v1/requests/{rid}/cancel", json={"reason": "Synthetic cleanup"}, headers=auth).status_code == 200


def test_export_and_purge_preserve_other_tenants_and_pending_records():
    tenant_id, slug, auth, body = tenant_fixture()
    foreign_id, _, foreign_auth, foreign_body = tenant_fixture()
    pending, complete = create(auth, body), create(auth, body)
    foreign = create(foreign_auth, foreign_body)
    cancel(auth, complete)
    cancel(foreign_auth, foreign)
    with SessionLocal() as db:
        for rid in (complete, foreign):
            db.get(models.Ask, rid).created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        db.commit()
    path = f"/v1/tenants/{tenant_id}/records"
    assert client.get(path).status_code == 401
    first = client.get(path + "?limit=1", headers=ADMIN_AUTH)
    assert len(first.json()) == 1 and first.headers["X-HCA-Next-Cursor"]
    second = client.get(path + "?after=" + first.headers["X-HCA-Next-Cursor"], headers=ADMIN_AUTH)
    assert {r["request_id"] for r in first.json() + second.json()} == {pending, complete}
    purge = {"confirm_slug": "wrong", "before": datetime.now(timezone.utc).isoformat()}
    assert client.post(path + "/purge", json=purge, headers=ADMIN_AUTH).status_code == 422
    purge["confirm_slug"] = slug
    assert client.post(path + "/purge", json=purge, headers=ADMIN_AUTH).json() == {"purged": 1}
    assert client.get(f"/v1/requests/{pending}", headers=auth).status_code == 200
    assert client.get(f"/v1/requests/{complete}", headers=auth).status_code == 404
    assert client.get(f"/v1/requests/{foreign}", headers=foreign_auth).status_code == 200
    with SessionLocal() as db:
        assert not db.query(models.Event).filter(models.Event.tenant_id == tenant_id,
            models.Event.data["request_id"].as_string() == complete).count()
        assert db.query(models.AuditRow).filter_by(tenant_id=foreign_id).count() == 1


def test_purge_is_bounded_and_refuses_future_or_naive_cutoffs():
    tenant_id, slug, auth, body = tenant_fixture()
    for _ in range(3):
        cancel(auth, create(auth, body))
    endpoint = f"/v1/tenants/{tenant_id}/records/purge"
    purge = {"confirm_slug": slug, "before": datetime.now(timezone.utc).isoformat(), "limit": 1}
    assert client.post(endpoint, json=purge, headers=ADMIN_AUTH).json() == {"purged": 1}
    with SessionLocal() as db:
        assert db.query(models.Ask).filter_by(tenant_id=tenant_id).count() == 2
    for before in (datetime.now().isoformat(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
        assert client.post(endpoint, json=purge | {"before": before}, headers=ADMIN_AUTH).status_code == 422


def seed_delivery(db, tenant_id, ask_id, destination_id, delivery_id, state, at):
    db.add(models.WebhookDelivery(id=delivery_id, tenant_id=tenant_id, ask_id=ask_id, destination_id=destination_id,
        event="request.cancelled", payload={"request_id": ask_id}, state=state, attempts=1, created_at=at,
        delivered_at=at if state == "delivered" else None, dead_at=at if state == "dead" else None))
    db.add(models.WebhookAttempt(id="attempt-" + delivery_id, ask_id=ask_id, delivery_id=delivery_id,
        event="request.cancelled", url="https://example.test", attempt=1, ok=state == "delivered", attempted_at=at))


def seed_destination(db, tenant_id, caller, destination_id, now):
    db.add(models.WebhookDestination(id=destination_id, tenant_id=tenant_id, caller=caller,
        url="https://example.test", secret_prefix="synthetic", secret_hash="synthetic", created_at=now))
    db.flush()


def test_explicit_purge_preserves_queued_delivery_and_current_job_lease():
    tenant_id, slug, auth, body = tenant_fixture()
    queued, leased, removable = [create(auth, body) for _ in range(3)]
    for rid in (queued, leased, removable):
        cancel(auth, rid)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        destination_id = "destination-" + uuid.uuid4().hex
        seed_destination(db, tenant_id, body["caller"], destination_id, now)
        seed_delivery(db, tenant_id, queued, destination_id, "queued-" + uuid.uuid4().hex, "queued", now)
        job = db.execute(select(models.ScheduledJob).where(models.ScheduledJob.ask_id == leased)).scalars().first()
        job.lease_owner = "synthetic-worker"
        job.lease_expires_at = now + timedelta(minutes=1)
        db.commit()
    response = client.post(f"/v1/tenants/{tenant_id}/records/purge", headers=ADMIN_AUTH,
        json={"confirm_slug": slug, "before": datetime.now(timezone.utc).isoformat()})
    assert response.json() == {"purged": 1}
    with SessionLocal() as db:
        assert db.get(models.Ask, queued) is not None
        assert db.get(models.Ask, leased) is not None
        assert db.get(models.Ask, removable) is None


def test_retention_prunes_transient_history_and_preserves_audits_and_active_work():
    tenant_id, _, auth, body = tenant_fixture()
    rid = create(auth, body)
    cancel(auth, rid)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old = now - timedelta(days=100)
    dest_id = "destination-" + uuid.uuid4().hex
    prefix = uuid.uuid4().hex
    with SessionLocal() as db:
        seed_destination(db, tenant_id, body["caller"], dest_id, old)
        for state in ("delivered", "dead", "queued"):
            seed_delivery(db, tenant_id, rid, dest_id, prefix + state, state, old)
        db.add(models.ScheduledJob(id=prefix + "done", tenant_id=tenant_id, ask_id=rid, kind="expire",
            run_at=old, done_at=old, created_at=old))
        db.add(models.ScheduledJob(id=prefix + "active", tenant_id=tenant_id, ask_id=rid, kind="webhook",
            ref_id=prefix + "queued", run_at=old, created_at=old))
        db.add(models.WebhookDestinationEvent(id=prefix + "registry", tenant_id=tenant_id,
            destination_id=dest_id, action="register", actor=body["caller"], at=old))
        db.add(models.Event(tenant_id=tenant_id, event="request.created", data={"request_id": rid}, created_at=old))
        db.add(RateLimitBucket(identity=prefix, window=1, count=1))
        db.commit()
        removed = retention.cleanup(db, now=now)
        assert removed["webhook_deliveries"] == 2 and removed["webhook_destination_events"] == 1
        assert removed["rate_limit_buckets"] == 1
        assert db.get(models.WebhookDelivery, prefix + "queued") is not None
        assert db.get(models.WebhookAttempt, "attempt-" + prefix + "queued") is not None
        assert db.get(models.ScheduledJob, prefix + "active") is not None
        assert db.get(models.ScheduledJob, prefix + "done") is None
        assert db.query(models.AuditRow).filter_by(ask_id=rid).count() == 1
        assert db.get(models.Ask, rid) is not None
        assert db.get(models.WebhookDestination, dest_id) is not None
        assert not any(retention.cleanup(db, now=now).values())


def test_retention_limits_each_table_and_covers_configured_replay_window(monkeypatch):
    tenant_id, _, _, _ = tenant_fixture()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        for _ in range(3):
            db.add(models.Event(tenant_id=tenant_id, event="request.created", data={},
                created_at=now - timedelta(days=40)))
        recent = models.Event(tenant_id=tenant_id, event="request.created", data={}, created_at=now - timedelta(days=10))
        db.add(recent)
        db.commit()
        recent_id = recent.id
        monkeypatch.setattr(events, "REPLAY_WINDOW_SECONDS", 30 * 86400)
        assert retention.cleanup(db, now=now, batch=2)["events"] == 2
        assert db.get(models.Event, recent_id) is not None
        assert db.query(models.Event).filter_by(tenant_id=tenant_id).count() == 2
        assert retention.cleanup(db, now=now, batch=2)["events"] == 1


def test_idempotency_retirement_is_bounded():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    key = uuid.uuid4().hex
    with SessionLocal() as db:
        for n in range(3):
            db.add(models.IdempotencyKey(tenant_id="default", scope=key, route="create", key=str(n),
                request_hash="synthetic", status_code=201, response={}, created_at=now - timedelta(days=2)))
        db.commit()
        assert idempotency.sweep(db, now=now, batch=1) == 1
        assert db.query(models.IdempotencyKey).filter_by(scope=key).count() == 2


def test_pruned_or_truncated_stream_requires_client_reconciliation(monkeypatch):
    tenant_id, _, _, _ = tenant_fixture()
    env = events.publish("request.answered", {}, tenant_id=tenant_id)
    with SessionLocal() as db:
        db.execute(delete(models.Event).where(models.Event.tenant_id == tenant_id))
        db.commit()
        assert events.replay_gap(db, tenant_id=tenant_id, device_id="synthetic", after_id=env["id"]) is not None
    async def scenario():
        source = inbox_api.inbox_event_source(models.Device(id="synthetic", tenant_id=tenant_id),
            authorized_fn=lambda _: True, last_event_id=env["id"])
        try:
            assert await anext(source) == ": connected\n\n"
            assert "stream.reset" in await anext(source)
        finally:
            await source.aclose()
    asyncio.run(scenario())


def test_reconciliation_bounds_unscheduled_backlog_without_starvation():
    from app import jobs
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tenant_id, _, _, body = tenant_fixture()
    with SessionLocal() as db:
        # The first 500 are already scheduled. Candidate filtering must skip
        # them before applying the batch limit, rather than rescan them forever.
        asks = []
        for n in range(1002):
            ask = models.Ask(id=f"backlog-{tenant_id}-{n:04d}", tenant_id=tenant_id, caller=body["caller"],
                audience="owner", title="Synthetic backlog", control="chat", suggested={}, deliver_to={"mode": "poll"},
                state="queued", expiry_policy="auto_deny", created_at=now - timedelta(days=2),
                expires_at=now - timedelta(days=1))
            asks.append(ask)
            db.add(ask)
        db.flush()
        for ask in asks[:500]:
            jobs.schedule(db, tenant_id=tenant_id, ask_id=ask.id, kind="expire", run_at=now - timedelta(days=1))
        # A future-dated open job is pulled forward without duplicating it.
        jobs.schedule(db, tenant_id=tenant_id, ask_id=asks[500].id, kind="expire", run_at=now + timedelta(days=1))
        db.commit()
        assert jobs.reconcile(db, now=now) == 500
        assert jobs.reconcile(db, now=now) == 2
        assert jobs.reconcile(db, now=now) == 0
        assert db.query(models.ScheduledJob).filter_by(tenant_id=tenant_id).count() == len(asks)
