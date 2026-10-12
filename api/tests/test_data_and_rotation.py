import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app import models, webhook_crypto
from app.apikeys import issue_api_key, verify_api_key
from authhelper import ADMIN_AUTH

client = TestClient(app)


def test_versioned_encryption_survives_pepper_rotation_and_old_key_overlap(monkeypatch):
    def encoded(byte):
        return base64.urlsafe_b64encode(byte * 32).decode()
    monkeypatch.setenv("HCA_WEBHOOK_ENCRYPTION_KEYS", json.dumps({"first": encoded(b"a")}))
    monkeypatch.setenv("HCA_WEBHOOK_CURRENT_KEY_ID", "first")
    cipher = webhook_crypto.encrypt("synthetic secret", destination_id="test")
    assert cipher.startswith("aesgcm2$first$")
    monkeypatch.setenv("HCA_API_KEY_PEPPER", "new-pepper-01234567890123456789012")
    monkeypatch.setenv("HCA_WEBHOOK_ENCRYPTION_KEYS", json.dumps({"first": encoded(b"a"), "second": encoded(b"b")}))
    monkeypatch.setenv("HCA_WEBHOOK_CURRENT_KEY_ID", "second")
    assert webhook_crypto.decrypt(cipher, destination_id="test") == "synthetic secret"
    with pytest.raises(webhook_crypto.SecretUnreadable):
        webhook_crypto.decrypt(cipher, destination_id="different")
    monkeypatch.setenv("HCA_WEBHOOK_ENCRYPTION_KEYS", json.dumps({"second": encoded(b"b")}))
    with pytest.raises(webhook_crypto.SecretUnreadable):
        webhook_crypto.decrypt(cipher, destination_id="test")


def test_previous_pepper_verifies_once_then_rehashes(monkeypatch):
    old = "old-pepper-01234567890123456789012"
    monkeypatch.setenv("HCA_API_KEY_PEPPER", old)
    with SessionLocal() as db:
        token = issue_api_key(db, "rotation-" + uuid.uuid4().hex)["api_key"]
        original = verify_api_key(db, token).key_hash
    monkeypatch.setenv("HCA_API_KEY_PEPPER", "new-pepper-01234567890123456789012")
    monkeypatch.setenv("HCA_API_KEY_PREVIOUS_PEPPERS", json.dumps([old]))
    with SessionLocal() as db:
        assert verify_api_key(db, token).key_hash != original
    monkeypatch.delenv("HCA_API_KEY_PREVIOUS_PEPPERS")
    with SessionLocal() as db:
        assert verify_api_key(db, token) is not None


def test_export_and_purge_are_tenant_scoped_and_preserve_pending():
    suffix = uuid.uuid4().hex
    with SessionLocal() as db:
        tenant = models.Tenant(id="data-" + suffix, slug=suffix, name="Synthetic", created_at=datetime.now(timezone.utc))
        db.add(tenant)
        db.commit()
        tenant_id = tenant.id
        auth = {"Authorization": "Bearer " + issue_api_key(db, "data-agent-" + suffix, tenant_id)["api_key"]}
    body = {"caller": "data-agent-" + suffix, "audience": "owner", "title": "Synthetic", "control": "chat",
            "options_or_fields": [], "suggested": {}, "deliver_to": {"mode": "poll"}}
    pending = client.post("/v1/requests", json=body, headers=auth).json()["request_id"]
    complete = client.post("/v1/requests", json=body, headers=auth).json()["request_id"]
    assert client.post(f"/v1/requests/{complete}/cancel", json={"reason": "Synthetic cleanup"}, headers=auth).status_code == 200
    with SessionLocal() as db:
        db.get(models.Ask, complete).created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        db.commit()
    path = f"/v1/tenants/{tenant_id}/records"
    assert client.get(path).status_code == 401
    first = client.get(path + "?limit=1", headers=ADMIN_AUTH)
    assert len(first.json()) == 1 and first.headers["X-HCA-Next-Cursor"]
    second = client.get(path + "?after=" + first.headers["X-HCA-Next-Cursor"], headers=ADMIN_AUTH)
    assert {r["request_id"] for r in first.json() + second.json()} == {pending, complete}
    purge = {"confirm_slug": "wrong", "before": datetime.now(timezone.utc).isoformat()}
    assert client.post(path + "/purge", json=purge, headers=ADMIN_AUTH).status_code == 422
    purge["confirm_slug"] = suffix
    assert client.post(path + "/purge", json=purge, headers=ADMIN_AUTH).json() == {"purged": 1}
    assert client.get(f"/v1/requests/{pending}", headers=auth).status_code == 200
    assert client.get(f"/v1/requests/{complete}", headers=auth).status_code == 404
    with SessionLocal() as db:
        assert not db.query(models.Event).filter(models.Event.tenant_id == tenant_id,
            models.Event.data["request_id"].as_string() == complete).count()
