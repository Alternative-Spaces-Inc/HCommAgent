#!/usr/bin/env python3
"""Verify then rewrap stored webhook secrets; dry-run unless --apply is given.

Use an operator-managed DATABASE_URL and encryption environment. Both current
and previous ciphertext columns, including revoked destinations, are covered.
A read-only preflight verifies every ciphertext before apply begins. Apply
re-verifies rows under locks and commits bounded batches; concurrent registry
changes must use the same versioned keyring. No signing secret is rotated.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from sqlalchemy import select, update  # noqa: E402 -- standalone script resolves repository package
from app import models, webhook_crypto  # noqa: E402
from app.apikeys import previous_peppers  # noqa: E402

CIPHERTEXT_FIELDS = ("secret_enc", "previous_secret_enc")


class MigrationFailure(RuntimeError):
    def __init__(self, committed_batches, error_type):
        super().__init__("encryption rewrap failed")
        self.committed_batches = committed_batches
        self.error_type = error_type


def _verified_values(row, target_key_id):
    verified = []
    for field in CIPHERTEXT_FIELDS:
        stored = getattr(row, field)
        if stored is None:
            continue
        plaintext = webhook_crypto.decrypt(stored, destination_id=row.id)
        needs_rewrap = not stored.startswith(f"aesgcm2${target_key_id}$")
        verified.append((field, plaintext, needs_rewrap))
    return verified


def _batch(db, after, batch_size, *, lock=False):
    statement = select(models.WebhookDestination).where(models.WebhookDestination.id > after)
    statement = statement.order_by(models.WebhookDestination.id).limit(batch_size)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return db.execute(statement).scalars().all()


def rewrap(session_factory, *, apply=False, batch_size=200):
    if not 1 <= batch_size <= 500:
        raise ValueError("batch-size must be 1 to 500")
    keys, target_key_id = webhook_crypto.keyring()
    previous_peppers()
    if not keys:
        raise RuntimeError("independent encryption keyring is required to rewrap")
    counts = {"mode": "apply" if apply else "dry-run", "target_key_id": target_key_id,
              "destinations_scanned": 0, "ciphertexts_verified": 0, "ciphertexts_to_rewrap": 0,
              "destinations_rewrapped": 0, "committed_batches": 0}
    after = ""
    try:
        # A corrupt or unavailable ciphertext anywhere blocks apply before
        # its first commit, including an expired previous/grace secret.
        while True:
            with session_factory() as db:
                rows = _batch(db, after, batch_size)
                if not rows:
                    break
                for row in rows:
                    values = _verified_values(row, target_key_id)
                    counts["destinations_scanned"] += 1
                    counts["ciphertexts_verified"] += len(values)
                    counts["ciphertexts_to_rewrap"] += sum(needed for _, _, needed in values)
                after = rows[-1].id
        if not apply:
            return counts
        after = ""
        while True:
            with session_factory() as db:
                rows = _batch(db, after, batch_size, lock=True)
                if not rows:
                    break
                # Authenticate every column in this batch before staging
                # changes, then commit both columns together per destination.
                verified = [(row, _verified_values(row, target_key_id)) for row in rows]
                changed = 0
                for row, values in verified:
                    replacements = {}
                    for field, plaintext, needed in values:
                        if needed:
                            replacements[field] = webhook_crypto.encrypt(plaintext, destination_id=row.id)
                    if replacements:
                        # SQLite ignores FOR UPDATE. Compare both original
                        # columns before replacing either, so a concurrent
                        # signing-secret rotation cannot be overwritten.
                        result = db.execute(update(models.WebhookDestination).where(
                            models.WebhookDestination.id == row.id,
                            models.WebhookDestination.secret_enc == row.secret_enc,
                            models.WebhookDestination.previous_secret_enc == row.previous_secret_enc,
                        ).values(**replacements).execution_options(synchronize_session=False))
                        if result.rowcount != 1:
                            raise RuntimeError("destination signing material changed during encryption rewrap")
                        changed += 1
                after = rows[-1].id
                db.commit()
                counts["destinations_rewrapped"] += changed
                counts["committed_batches"] += 1
        return counts
    except Exception as exc:
        # Session context rollback preserves this batch. Earlier successful
        # batches may remain committed; rerunning is safe and resumable.
        raise MigrationFailure(counts["committed_batches"], type(exc).__name__) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit verified ciphertext rewrap batches")
    parser.add_argument("--batch-size", type=int, default=200, help="Rows per transaction, 1 to 500")
    args = parser.parse_args(argv)
    if not os.environ.get("DATABASE_URL"):
        print(json.dumps({"error": "DATABASE_URL must be explicitly configured"}))
        return 1
    try:
        from app.db import SessionLocal
        result = rewrap(SessionLocal, apply=args.apply, batch_size=args.batch_size)
        print(json.dumps(result, sort_keys=True))
        return 0
    except MigrationFailure as exc:
        print(json.dumps({"error": "encryption rewrap failed", "error_type": exc.error_type,
                          "committed_batches": exc.committed_batches}, sort_keys=True))
    except Exception as exc:
        # Configuration/database exceptions can contain credentials. Report
        # the type only; never print exception text, row IDs or ciphertext.
        print(json.dumps({"error": "encryption rewrap failed", "error_type": type(exc).__name__}, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
