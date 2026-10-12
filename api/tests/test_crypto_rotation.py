"""Synthetic encryption/verification transitions preserve old data and revocation."""
import base64
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.sql.dml import Update

from app import apikeys, models, webhook_crypto
from app.config import validate_environment
from app.db import SessionLocal
from app.main import app  # noqa: F401 -- eager registration of metadata

OLD_PEPPER = "synthetic-old-pepper-01234567890123456789"
NEW_PEPPER = "synthetic-new-pepper-01234567890123456789"


def ring(**values):
    return json.dumps({key: base64.urlsafe_b64encode(value * 32).decode() for key, value in values.items()})


def test_optional_keyring_preserves_legacy_then_survives_pepper_rotation(monkeypatch):
    monkeypatch.delenv("HCA_WEBHOOK_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("HCA_WEBHOOK_CURRENT_KEY_ID", raising=False)
    monkeypatch.setenv("HCA_API_KEY_PEPPER", OLD_PEPPER)
    legacy = webhook_crypto.encrypt("synthetic secret", destination_id="synthetic")
    assert legacy.startswith("aesgcm$")
    monkeypatch.setenv("HCA_WEBHOOK_ENCRYPTION_KEYS", ring(first=b"a"))
    monkeypatch.setenv("HCA_WEBHOOK_CURRENT_KEY_ID", "first")
    assert webhook_crypto.decrypt(legacy, destination_id="synthetic") == "synthetic secret"
    current = webhook_crypto.encrypt("synthetic secret", destination_id="synthetic")
    assert current.startswith("aesgcm2$first$")
    monkeypatch.setenv("HCA_API_KEY_PEPPER", NEW_PEPPER)
    monkeypatch.setenv("HCA_API_KEY_PREVIOUS_PEPPERS", json.dumps([OLD_PEPPER]))
    assert webhook_crypto.decrypt(legacy, destination_id="synthetic") == "synthetic secret"
    assert webhook_crypto.decrypt(current, destination_id="synthetic") == "synthetic secret"
    monkeypatch.delenv("HCA_API_KEY_PREVIOUS_PEPPERS")
    with pytest.raises(webhook_crypto.SecretUnreadable):
        webhook_crypto.decrypt(legacy, destination_id="synthetic")
    assert webhook_crypto.decrypt(current, destination_id="synthetic") == "synthetic secret"


def test_keyring_overlap_and_associated_data_are_authenticated(monkeypatch):
    monkeypatch.setenv("HCA_WEBHOOK_ENCRYPTION_KEYS", ring(first=b"a"))
    monkeypatch.setenv("HCA_WEBHOOK_CURRENT_KEY_ID", "first")
    stored = webhook_crypto.encrypt("synthetic secret", destination_id="correct")
    monkeypatch.setenv("HCA_WEBHOOK_ENCRYPTION_KEYS", ring(first=b"a", second=b"b"))
    monkeypatch.setenv("HCA_WEBHOOK_CURRENT_KEY_ID", "second")
    assert webhook_crypto.decrypt(stored, destination_id="correct") == "synthetic secret"
    with pytest.raises(webhook_crypto.SecretUnreadable):
        webhook_crypto.decrypt(stored, destination_id="wrong")
    monkeypatch.setenv("HCA_WEBHOOK_ENCRYPTION_KEYS", ring(second=b"b"))
    with pytest.raises(webhook_crypto.SecretUnreadable):
        webhook_crypto.decrypt(stored, destination_id="correct")


@pytest.mark.parametrize("config", [
    {"HCA_WEBHOOK_ENCRYPTION_KEYS": "{}", "HCA_WEBHOOK_CURRENT_KEY_ID": "missing"},
    {"HCA_WEBHOOK_ENCRYPTION_KEYS": "[]"},
    {"HCA_WEBHOOK_ENCRYPTION_KEYS": ring(first=b"a")},
    {"HCA_WEBHOOK_ENCRYPTION_KEYS": '{"first":"bad"}', "HCA_WEBHOOK_CURRENT_KEY_ID": "first"},
    {"HCA_WEBHOOK_ENCRYPTION_KEYS": '{"first":"bad","first":"bad"}', "HCA_WEBHOOK_CURRENT_KEY_ID": "first"},
    {"HCA_API_KEY_PREVIOUS_PEPPERS": json.dumps([OLD_PEPPER] * 4)},
    {"HCA_API_KEY_PREVIOUS_PEPPERS": json.dumps(["short"])},
    {"HCA_API_KEY_PREVIOUS_PEPPERS": "{}"},
])
def test_invalid_transition_configuration_fails_at_startup_without_echoing_values(config):
    with pytest.raises(RuntimeError) as caught:
        validate_environment({"HCA_ENV": "development"} | config)
    assert OLD_PEPPER not in str(caught.value)
    assert "bad" not in str(caught.value)


def test_previous_pepper_success_upgrades_and_revocation_stays_effective(monkeypatch):
    monkeypatch.setenv("HCA_API_KEY_PEPPER", OLD_PEPPER)
    with SessionLocal() as db:
        issued = apikeys.issue_api_key(db, "rotate-" + uuid.uuid4().hex)
        before = apikeys.verify_api_key(db, issued["api_key"]).key_hash
    monkeypatch.setenv("HCA_API_KEY_PEPPER", NEW_PEPPER)
    monkeypatch.setenv("HCA_API_KEY_PREVIOUS_PEPPERS", json.dumps([OLD_PEPPER]))
    with SessionLocal() as db:
        verified = apikeys.verify_api_key(db, issued["api_key"])
        assert verified.key_hash != before and verified.tenant_id == "default"
        verified.revoked_at = datetime.now(timezone.utc)
        db.commit()
    monkeypatch.delenv("HCA_API_KEY_PREVIOUS_PEPPERS")
    with SessionLocal() as db:
        assert apikeys.verify_api_key(db, issued["api_key"]) is None


@pytest.mark.parametrize("change", ["rotate", "revoke"])
def test_hash_upgrade_cannot_overwrite_concurrent_credential_change(monkeypatch, change):
    caller = "race-" + uuid.uuid4().hex
    monkeypatch.setenv("HCA_API_KEY_PEPPER", OLD_PEPPER)
    with SessionLocal() as db:
        issued = apikeys.issue_api_key(db, caller)
    monkeypatch.setenv("HCA_API_KEY_PEPPER", NEW_PEPPER)
    monkeypatch.setenv("HCA_API_KEY_PREVIOUS_PEPPERS", json.dumps([OLD_PEPPER]))
    replacement = []
    with SessionLocal() as db:
        original_execute = db.execute
        changed = [False]
        def execute(statement, *args, **kwargs):
            if isinstance(statement, Update) and statement.table.name == "api_keys" and not changed[0]:
                changed[0] = True
                with SessionLocal() as other:
                    if change == "rotate":
                        replacement.append(apikeys.issue_api_key(other, caller)["api_key"])
                    else:
                        row = other.query(models.ApiKey).filter_by(caller=caller).one()
                        row.revoked_at = datetime.now(timezone.utc)
                        other.commit()
            return original_execute(statement, *args, **kwargs)
        monkeypatch.setattr(db, "execute", execute)
        assert apikeys.verify_api_key(db, issued["api_key"]) is None
    with SessionLocal() as db:
        assert apikeys.verify_api_key(db, issued["api_key"]) is None
        if replacement:
            assert apikeys.verify_api_key(db, replacement[0]) is not None


def test_simultaneous_successful_pepper_upgrades_reuse_the_winner(monkeypatch):
    caller = "upgrade-race-" + uuid.uuid4().hex
    monkeypatch.setenv("HCA_API_KEY_PEPPER", OLD_PEPPER)
    with SessionLocal() as db:
        issued = apikeys.issue_api_key(db, caller)
    monkeypatch.setenv("HCA_API_KEY_PEPPER", NEW_PEPPER)
    monkeypatch.setenv("HCA_API_KEY_PREVIOUS_PEPPERS", json.dumps([OLD_PEPPER]))
    with SessionLocal() as db:
        execute = db.execute
        upgraded = [False]
        def concurrent(statement, *args, **kwargs):
            if isinstance(statement, Update) and statement.table.name == "api_keys" and not upgraded[0]:
                upgraded[0] = True
                with SessionLocal() as other:
                    assert apikeys.verify_api_key(other, issued["api_key"]) is not None
            return execute(statement, *args, **kwargs)
        monkeypatch.setattr(db, "execute", concurrent)
        assert apikeys.verify_api_key(db, issued["api_key"]) is not None
