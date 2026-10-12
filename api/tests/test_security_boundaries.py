import asyncio
from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.db import SessionLocal
from app import models, nonces, devicesig, googleauth, events
from app.auth import device_is_authorized
from app.config import validate_environment
from app.inbox_api import inbox_event_source
from app.tenancy import owner_emails_for



def test_future_timestamp_nonce_survives_entire_acceptance_window():
    now = datetime.now(timezone.utc)
    device = "probe-" + uuid.uuid4().hex
    assert nonces.check_and_record(device, "future", now=now)
    nonces.prune(now=now + timedelta(seconds=301))
    assert devicesig.timestamp_is_fresh(str(int(now.timestamp()) + 300), now=now.timestamp() + 301)
    assert not nonces.check_and_record(device, "future", now=now + timedelta(seconds=301))
    nonces.prune(now=now + timedelta(seconds=602))
    assert nonces.check_and_record(device, "future", now=now + timedelta(seconds=602))


def test_empty_owner_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("HCA_GOOGLE_CLIENT_ID", "client")
    monkeypatch.delenv("HCA_OWNER_EMAILS", raising=False)
    googleauth.set_fetcher(lambda _: (200, {"aud": "client", "sub": "subject", "email": "any@example.test",
                                          "email_verified": True, "expires_in": 600}))
    try:
        with pytest.raises(googleauth.GoogleAuthError, match="not an owner"):
            googleauth.verify_access_token("synthetic", allowed_emails=None)
    finally:
        googleauth.set_fetcher(None)


def test_removing_last_owner_does_not_restore_environment_fallback(monkeypatch):
    monkeypatch.setenv("HCA_REQUIRE_GOOGLE_LINK", "1")
    suffix = uuid.uuid4().hex
    with SessionLocal() as db:
        tenant = models.Tenant(id="tenant-" + suffix, slug=suffix, name="synthetic", created_at=datetime.now(timezone.utc))
        db.add(tenant)
        db.flush()
        email = suffix + "@example.test"
        db.add(models.TenantOwner(tenant_id=tenant.id, email=email, created_at=datetime.now(timezone.utc)))
        device = models.Device(id="dev-" + suffix, tenant_id=tenant.id, label="synthetic", surface="browser_overlay",
                               public_key_pem="unused", google_sub="subject", google_email=email,
                               created_at=datetime.now(timezone.utc))
        db.add(device)
        db.commit()
        assert device_is_authorized(db, device)
        owner = db.query(models.TenantOwner).filter_by(tenant_id=tenant.id).one()
        owner.removed_at = datetime.now(timezone.utc)
        db.commit()
        assert owner_emails_for(db, tenant.id) == set()
        assert not device_is_authorized(db, device)
        tenant.disabled_at = datetime.now(timezone.utc)
        db.commit()
        monkeypatch.setenv("HCA_REQUIRE_GOOGLE_LINK", "0")
        assert not device_is_authorized(db, device)


def test_busy_stream_revocation_has_bounded_latency():
    async def run():
        device = models.Device(id="synthetic-stream", tenant_id="default")
        allowed = [True]
        checks = []
        def authorize(_):
            checks.append(True)
            return allowed[0]
        source = inbox_event_source(device, keepalive_seconds=.02, authorized_fn=authorize)
        assert await anext(source) == ": connected\n\n"
        allowed[0] = False
        deadline = asyncio.get_running_loop().time() + .2
        try:
            while asyncio.get_running_loop().time() < deadline:
                events.publish("request.answered", {"request_id": "busy"}, tenant_id="default")
                chunk = await asyncio.wait_for(anext(source), .2)
                if "stream.closed" in chunk:
                    assert len(checks) >= 2
                    return
                await asyncio.sleep(.005)
            pytest.fail("busy revoked stream remained open")
        finally:
            await source.aclose()
    asyncio.run(run())


def test_production_configuration_requires_secure_defaults():
    with pytest.raises(RuntimeError, match="Invalid production"):
        validate_environment({"HCA_ENV": "production"})
    valid = {"HCA_ENV": "production", "DATABASE_URL": "postgresql://synthetic",
             "HCA_ADMIN_TOKEN": "a" * 32, "HCA_API_KEY_PEPPER": "p" * 32, "SWEEP_TRIGGER_TOKEN": "s" * 32,
             "HCA_REQUIRE_GOOGLE_LINK": "1", "HCA_GOOGLE_CLIENT_ID": "client",
}
    validate_environment(valid)
    with pytest.raises(RuntimeError):
        validate_environment(valid | {"HCA_WEBHOOK_ALLOW_INSECURE": "1"})






def test_managed_service_cannot_opt_out_of_production_authentication():
    with pytest.raises(RuntimeError, match="Managed service"):
        validate_environment({"K_SERVICE": "synthetic", "HCA_ENV": "development"})
