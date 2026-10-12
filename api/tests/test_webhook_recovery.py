"""Finite retry policy and fenced internal-failure dead letters."""
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import jobs, models, webhook_crypto, webhook_destinations as registry, webhooks
from app.config import validate_environment
from app.db import SessionLocal
from app.main import app  # noqa: F401 -- register all metadata


def destination_fixture():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    suffix = uuid.uuid4().hex
    with SessionLocal() as db:
        ask = models.Ask(id="recovery-ask-" + suffix, tenant_id="default", caller="recovery-" + suffix,
            audience="owner", title="Synthetic", control="chat", suggested={}, deliver_to={"mode": "poll"},
            state="cancelled", created_at=now)
        dest = models.WebhookDestination(id="recovery-dest-" + suffix, tenant_id="default", caller=ask.caller,
            url="https://example.test", secret_prefix="synthetic", secret_hash="synthetic", created_at=now,
            secret_enc=webhook_crypto.encrypt("synthetic secret", destination_id="recovery-dest-" + suffix))
        db.add_all([ask, dest])
        db.flush()
        delivery = models.WebhookDelivery(id="recovery-delivery-" + suffix, tenant_id="default", ask_id=ask.id,
            destination_id=dest.id, event="request.cancelled", payload={}, state="queued", attempts=0, created_at=now)
        db.add(delivery)
        job = jobs.schedule(db, tenant_id="default", ask_id=ask.id, kind="webhook", ref_id=delivery.id, run_at=now)
        db.commit()
        return dest.id, ask.caller, delivery.id, job.id


@pytest.mark.parametrize("value", ["inf", "nan", "-1", "", "1,,2", "oops", "86401", ",".join(["1"] * 17), ",".join(["86400"] * 8)])
def test_invalid_retry_policy_fails_at_startup_and_before_send(monkeypatch, value):
    with pytest.raises(RuntimeError, match="retry schedule"):
        validate_environment({"HCA_ENV": "development", "WEBHOOK_RETRY_SCHEDULE": value})
    monkeypatch.setenv("WEBHOOK_RETRY_SCHEDULE", value)
    monkeypatch.setattr(webhooks, "_post", lambda *args: pytest.fail("invalid policy must fail before HTTP"))
    with pytest.raises(RuntimeError, match="retry schedule"):
        webhooks.attempt_delivery("missing", SessionLocal)


def test_retry_policy_reports_default_and_custom_horizons():
    policy = webhooks.retry_policy({})
    assert policy["attempts"] == 8 and policy["nominal_wait_seconds"] == 6156
    assert policy["maximum_jittered_wait_seconds"] == pytest.approx(7387.2)
    custom = webhooks.retry_policy({"WEBHOOK_RETRY_SCHEDULE": "0,5,10"})
    assert custom["attempts"] == 4 and custom["nominal_wait_seconds"] == 15


def test_configured_schedule_controls_total_attempts(monkeypatch):
    _, _, delivery_id, _ = destination_fixture()
    monkeypatch.setenv("WEBHOOK_RETRY_SCHEDULE", "0,0")
    monkeypatch.setattr(webhooks, "_post", lambda *args: (503, "synthetic failure"))
    actions = []
    for _ in range(3):
        actions.extend(result["action"] for result in jobs.run_due(owner="synthetic") if result.get("delivery_id") == delivery_id)
    assert actions == ["retry_scheduled", "retry_scheduled", "dead"]
    with SessionLocal() as db:
        assert db.get(models.WebhookDelivery, delivery_id).attempts == 3


def test_internal_webhook_job_exhaustion_does_not_orphan_delivery(monkeypatch):
    _, _, delivery_id, job_id = destination_fixture()
    monkeypatch.setattr(jobs, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(webhooks, "_post", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic sensitive detail")))
    results = jobs.run_due(owner="synthetic")
    assert any(result.get("delivery_id") == delivery_id and result["action"] == "dead" for result in results)
    with SessionLocal() as db:
        delivery = db.get(models.WebhookDelivery, delivery_id)
        job = db.get(models.ScheduledJob, job_id)
        assert delivery.state == "dead" and job.done_at is not None and job.result["action"] == "dead"
        assert "RuntimeError" in delivery.last_error and "sensitive detail" not in delivery.last_error
        assert db.query(models.WebhookDestinationEvent).filter_by(destination_id=delivery.destination_id, action="delivery.dead").count() == 1


def test_internal_failure_dead_letter_cannot_overwrite_new_lease():
    _, _, delivery_id, job_id = destination_fixture()
    with SessionLocal() as db:
        first = jobs.claim_due(db, "first", limit=1, job_ids=[job_id])[0]
        stale = jobs.Lease(job_id, "first", first.attempts)
        row = db.get(models.ScheduledJob, job_id)
        row.lease_owner = "replacement"
        row.attempts += 1
        db.commit()
    with SessionLocal() as db:
        with pytest.raises(jobs.LeaseLost):
            webhooks.fail_job_delivery(db, delivery_id, "synthetic", datetime.now(timezone.utc), lease=stale)
    with SessionLocal() as db:
        assert db.get(models.WebhookDelivery, delivery_id).state == "queued"
        assert db.get(models.ScheduledJob, job_id).lease_owner == "replacement"


def test_repeated_rotation_preserves_promised_overlap_then_allows_boundary():
    destination_id, caller, _, _ = destination_fixture()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        _, first_secret = registry.rotate(db, tenant_id="default", caller=caller, destination_id=destination_id, now=now)
    with SessionLocal() as db:
        with pytest.raises(registry.Conflict):
            registry.rotate(db, tenant_id="default", caller=caller, destination_id=destination_id, now=now + timedelta(hours=1))
    with SessionLocal() as db:
        dest = db.get(models.WebhookDestination, destination_id)
        assert first_secret in registry.signing_secrets(dest, now=now + timedelta(hours=1))
        registry.rotate(db, tenant_id="default", caller=caller, destination_id=destination_id, now=now + timedelta(hours=24))


def test_concurrent_rotations_return_one_usable_secret_and_one_conflict():
    destination_id, caller, _, _ = destination_fixture()
    barrier = threading.Barrier(2)
    results = []
    def rotate():
        with SessionLocal() as db:
            barrier.wait(5)
            try:
                _, secret = registry.rotate(db, tenant_id="default", caller=caller, destination_id=destination_id)
                results.append(secret)
            except registry.Conflict:
                results.append(None)
    threads = [threading.Thread(target=rotate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len(results) == 2 and results.count(None) == 1
    with SessionLocal() as db:
        assert next(value for value in results if value is not None) in registry.signing_secrets(db.get(models.WebhookDestination, destination_id))
