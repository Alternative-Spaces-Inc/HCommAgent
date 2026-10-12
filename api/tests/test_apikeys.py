import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_apikeys.db")

from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app.apikeys import verify_api_key
from sqlalchemy import select
from authhelper import ADMIN_AUTH


client = TestClient(app)


def test_issue_key_returns_plaintext_once_and_verifies():
    resp = client.post("/v1/api-keys", json={"caller": "agent-keys-1"}, headers=ADMIN_AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["caller"] == "agent-keys-1"
    assert body["api_key"].startswith("hca_live_")
    assert body["prefix"] in body["api_key"]

    db = SessionLocal()
    try:
        row = verify_api_key(db, body["api_key"])
        assert row is not None
        assert row.caller == "agent-keys-1"
    finally:
        db.close()


def test_verify_rejects_a_wrong_secret_with_a_valid_prefix():
    resp = client.post("/v1/api-keys", json={"caller": "agent-keys-2"}, headers=ADMIN_AUTH)
    prefix = resp.json()["prefix"]
    tampered = f"hca_live_{prefix}_not-the-real-secret"

    db = SessionLocal()
    try:
        assert verify_api_key(db, tampered) is None
    finally:
        db.close()


def test_verify_rejects_an_unknown_prefix():
    db = SessionLocal()
    try:
        assert verify_api_key(db, "hca_live_doesnotexist_whatever") is None
        assert verify_api_key(db, "") is None
        assert verify_api_key(db, "not-even-the-right-format") is None
    finally:
        db.close()


def test_reissue_replaces_the_old_key_for_the_same_caller():
    first = client.post("/v1/api-keys", json={"caller": "agent-keys-3"}, headers=ADMIN_AUTH).json()
    second = client.post("/v1/api-keys", json={"caller": "agent-keys-3"}, headers=ADMIN_AUTH).json()
    assert first["api_key"] != second["api_key"]

    db = SessionLocal()
    try:
        assert verify_api_key(db, first["api_key"]) is None
        assert verify_api_key(db, second["api_key"]) is not None
    finally:
        db.close()


def test_revoked_key_does_not_verify():
    body = client.post("/v1/api-keys", json={"caller": "agent-keys-4"}, headers=ADMIN_AUTH).json()

    db = SessionLocal()
    try:
        row = verify_api_key(db, body["api_key"])
        assert row is not None
        from datetime import datetime, timezone
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
        assert verify_api_key(db, body["api_key"]) is None
    finally:
        db.close()


def test_list_and_revoke_by_prefix_are_admin_only_and_idempotent():
    issued = client.post("/v1/api-keys", json={"caller": "agent-keys-revoke"}, headers=ADMIN_AUTH).json()
    prefix, plaintext = issued["prefix"], issued["api_key"]

    assert client.get("/v1/api-keys").status_code == 401
    assert client.post(f"/v1/api-keys/{prefix}/revoke").status_code == 401

    listed = client.get("/v1/api-keys", headers=ADMIN_AUTH)
    assert listed.status_code == 200
    mine = [k for k in listed.json() if k["prefix"] == prefix]
    assert len(mine) == 1 and mine[0]["caller"] == "agent-keys-revoke" and mine[0]["revoked_at"] is None
    assert "api_key" not in mine[0] and "key_hash" not in mine[0]

    first = client.post(f"/v1/api-keys/{prefix}/revoke", headers=ADMIN_AUTH)
    assert first.status_code == 200 and first.json()["revoked_at"] is not None
    second = client.post(f"/v1/api-keys/{prefix}/revoke", headers=ADMIN_AUTH)
    assert second.status_code == 200 and second.json()["revoked_at"] == first.json()["revoked_at"]

    assert client.post("/v1/api-keys/nosuchprefix/revoke", headers=ADMIN_AUTH).status_code == 404

    db = SessionLocal()
    try:
        assert verify_api_key(db, plaintext) is None
    finally:
        db.close()


def test_revoked_key_is_refused_on_an_agent_route():
    issued = client.post("/v1/api-keys", json={"caller": "agent-keys-refused"}, headers=ADMIN_AUTH).json()
    agent = {"Authorization": f"Bearer {issued['api_key']}"}
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 404
    client.post(f"/v1/api-keys/{issued['prefix']}/revoke", headers=ADMIN_AUTH)
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 403


def test_stored_hash_is_peppered_hmac_not_plain_sha256():
    import hashlib
    from app import models
    resp = client.post("/v1/api-keys", json={"caller": "agent-keys-pepper"}, headers=ADMIN_AUTH)
    assert resp.status_code == 201
    key = resp.json()["api_key"]
    secret = "_".join(key.split("_")[3:])
    db = SessionLocal()
    try:
        row = db.execute(select(models.ApiKey).where(models.ApiKey.caller == "agent-keys-pepper")).scalar_one()
        assert row.key_hash.startswith("hmac256$")
        assert row.key_hash != hashlib.sha256(secret.encode()).hexdigest()
    finally:
        db.close()


def test_legacy_sha256_row_verifies_once_and_is_rehashed():
    import hashlib
    from app import models
    resp = client.post("/v1/api-keys", json={"caller": "agent-keys-legacy"}, headers=ADMIN_AUTH)
    key = resp.json()["api_key"]
    secret = "_".join(key.split("_")[3:])
    db = SessionLocal()
    try:
        row = db.execute(select(models.ApiKey).where(models.ApiKey.caller == "agent-keys-legacy")).scalar_one()
        row.key_hash = hashlib.sha256(secret.encode()).hexdigest()  # as minted before the pepper
        db.commit()
        assert verify_api_key(db, key) is not None
        db.refresh(row)
        assert row.key_hash.startswith("hmac256$")
        assert verify_api_key(db, key) is not None
        assert verify_api_key(db, key[:-1] + ("A" if key[-1] != "A" else "B")) is None
    finally:
        db.close()


def test_missing_pepper_refuses_to_hash(monkeypatch):
    import pytest
    from app.apikeys import _hash_secret
    monkeypatch.delenv("HCA_API_KEY_PEPPER", raising=False)
    with pytest.raises(RuntimeError):
        _hash_secret("anything")
