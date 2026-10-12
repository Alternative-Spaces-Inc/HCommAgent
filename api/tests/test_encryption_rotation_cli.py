"""Migration CLI defaults to verification and never emits signing material."""
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete

from app import models, webhook_crypto, webhook_destinations
from app.apikeys import _hash_secret
from app.db import SessionLocal
from app.main import app  # noqa: F401 -- register all metadata
from test_crypto_rotation import NEW_PEPPER, OLD_PEPPER, ring

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rotate_webhook_encryption.py"
spec = importlib.util.spec_from_file_location("hca_rotation_cli", SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.fixture(autouse=True)
def isolate_migration_records():
    with SessionLocal() as db:
        db.execute(delete(models.WebhookDestination))
        db.commit()


def seed(monkeypatch):
    monkeypatch.delenv("HCA_WEBHOOK_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("HCA_WEBHOOK_CURRENT_KEY_ID", raising=False)
    monkeypatch.setenv("HCA_API_KEY_PEPPER", OLD_PEPPER)
    ids = ["migration-" + uuid.uuid4().hex for _ in range(2)]
    originals = {}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        for n, destination_id in enumerate(ids):
            current = webhook_crypto.encrypt("synthetic current", destination_id=destination_id)
            previous = webhook_crypto.encrypt("synthetic previous", destination_id=destination_id)
            originals[destination_id] = (current, previous)
            db.add(models.WebhookDestination(id=destination_id, tenant_id="default", caller="synthetic-" + str(n),
                url="https://example.test", secret_prefix="synthetic", secret_hash="preserve-current-hash",
                previous_secret_hash="preserve-previous-hash", secret_enc=current, previous_secret_enc=previous,
                previous_valid_until=now - timedelta(days=1) if n else now + timedelta(days=1),
                revoked_at=now if n else None, created_at=now))
        db.commit()
    monkeypatch.setenv("HCA_API_KEY_PEPPER", NEW_PEPPER)
    monkeypatch.setenv("HCA_API_KEY_PREVIOUS_PEPPERS", json.dumps([OLD_PEPPER]))
    monkeypatch.setenv("HCA_WEBHOOK_ENCRYPTION_KEYS", ring(target=b"c"))
    monkeypatch.setenv("HCA_WEBHOOK_CURRENT_KEY_ID", "target")
    return ids, originals


def test_cli_defaults_dry_run_then_rewraps_both_columns_including_revoked(monkeypatch):
    ids, originals = seed(monkeypatch)
    command = [sys.executable, str(SCRIPT), "--batch-size", "1"]
    dry = subprocess.run(command, env=os.environ.copy(), capture_output=True, text=True, check=True)
    result = json.loads(dry.stdout)
    assert result["mode"] == "dry-run" and result["ciphertexts_to_rewrap"] == 4
    assert result["destinations_rewrapped"] == 0
    with SessionLocal() as db:
        for destination_id in ids:
            row = db.get(models.WebhookDestination, destination_id)
            assert (row.secret_enc, row.previous_secret_enc) == originals[destination_id]
    applied = subprocess.run([*command, "--apply"], env=os.environ.copy(), capture_output=True, text=True, check=True)
    result = json.loads(applied.stdout)
    assert result["destinations_rewrapped"] == 2 and result["committed_batches"] == 2
    for output in (dry.stdout + dry.stderr, applied.stdout + applied.stderr):
        assert "synthetic current" not in output and "synthetic previous" not in output
        assert not any(destination_id in output for destination_id in ids)
        assert not any(cipher in output for pair in originals.values() for cipher in pair)
    monkeypatch.delenv("HCA_API_KEY_PREVIOUS_PEPPERS")
    with SessionLocal() as db:
        for destination_id in ids:
            row = db.get(models.WebhookDestination, destination_id)
            assert row.secret_enc.startswith("aesgcm2$target$")
            assert row.previous_secret_enc.startswith("aesgcm2$target$")
            assert webhook_crypto.decrypt(row.secret_enc, destination_id=destination_id) == "synthetic current"
            assert webhook_crypto.decrypt(row.previous_secret_enc, destination_id=destination_id) == "synthetic previous"
            assert row.secret_hash == "preserve-current-hash" and row.previous_secret_hash == "preserve-previous-hash"
    assert cli.rewrap(SessionLocal, apply=True, batch_size=1)["destinations_rewrapped"] == 0


def test_corrupt_previous_column_blocks_apply_before_any_commit(monkeypatch):
    ids, originals = seed(monkeypatch)
    with SessionLocal() as db:
        db.get(models.WebhookDestination, ids[-1]).previous_secret_enc = originals[ids[0]][1]
        db.commit()
    with pytest.raises(cli.MigrationFailure) as failure:
        cli.rewrap(SessionLocal, apply=True, batch_size=1)
    assert failure.value.committed_batches == 0
    with SessionLocal() as db:
        assert db.get(models.WebhookDestination, ids[0]).secret_enc == originals[ids[0]][0]
        assert db.get(models.WebhookDestination, ids[-1]).secret_enc == originals[ids[-1]][0]


def test_apply_failure_rolls_back_its_entire_batch(monkeypatch):
    ids, originals = seed(monkeypatch)
    encrypt = webhook_crypto.encrypt
    calls = [0]
    def fail_second(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("synthetic secret-containing failure")
        return encrypt(*args, **kwargs)
    monkeypatch.setattr(webhook_crypto, "encrypt", fail_second)
    with pytest.raises(cli.MigrationFailure) as failure:
        cli.rewrap(SessionLocal, apply=True, batch_size=2)
    assert failure.value.committed_batches == 0
    with SessionLocal() as db:
        for destination_id in ids:
            row = db.get(models.WebhookDestination, destination_id)
            assert (row.secret_enc, row.previous_secret_enc) == originals[destination_id]


def test_sqlite_concurrent_signing_rotation_is_preserved_and_rewrap_refuses(monkeypatch):
    if SessionLocal.kw["bind"].dialect.name != "sqlite":
        pytest.skip("SQLite's ignored FOR UPDATE requires a conditional rewrap")
    ids, originals = seed(monkeypatch)
    destination_id = ids[0]
    with SessionLocal() as db:
        db.execute(delete(models.WebhookDestination).where(models.WebhookDestination.id == ids[1]))
        row = db.get(models.WebhookDestination, destination_id)
        row.previous_secret_enc = None
        row.previous_secret_hash = None
        row.previous_valid_until = None
        db.commit()
    encrypt = webhook_crypto.encrypt
    rotation = {}

    def rotate_after_snapshot(secret, **kwargs):
        # A real second transaction rotates after the migration authenticated
        # its old snapshot, before it stages its replacement ciphertext.
        if not rotation:
            rotation["entered"] = True
            with SessionLocal() as other:
                _, rotation["secret"] = webhook_destinations.rotate(other, tenant_id="default",
                    caller="synthetic-0", destination_id=destination_id)
        return encrypt(secret, **kwargs)

    monkeypatch.setattr(webhook_crypto, "encrypt", rotate_after_snapshot)
    with pytest.raises(cli.MigrationFailure) as failure:
        cli.rewrap(SessionLocal, apply=True, batch_size=1)
    assert failure.value.committed_batches == 0
    with SessionLocal() as db:
        row = db.get(models.WebhookDestination, destination_id)
        assert row.secret_hash == _hash_secret(rotation["secret"])
        assert webhook_crypto.decrypt(row.secret_enc, destination_id=destination_id) == rotation["secret"]
        assert row.previous_secret_enc == originals[destination_id][0]
        assert row.previous_valid_until > datetime.now(timezone.utc).replace(tzinfo=None)
    # Retrying observes the successful rotation and safely finishes the rewrap.
    monkeypatch.setattr(webhook_crypto, "encrypt", encrypt)
    assert cli.rewrap(SessionLocal, apply=True, batch_size=1)["destinations_rewrapped"] == 1


def test_cli_requires_explicit_database_and_keyring(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cli.main([]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "DATABASE_URL must be explicitly configured"
    monkeypatch.setenv("DATABASE_URL", "synthetic")
    monkeypatch.delenv("HCA_WEBHOOK_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("HCA_WEBHOOK_CURRENT_KEY_ID", raising=False)
    assert cli.main([]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error_type"] == "RuntimeError"
    assert "synthetic" not in output
