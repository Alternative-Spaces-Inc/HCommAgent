import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_apikeys.db")

from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine, SessionLocal
from app.apikeys import verify_api_key
from authhelper import ADMIN_AUTH

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

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
