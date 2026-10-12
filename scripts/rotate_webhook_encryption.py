#!/usr/bin/env python3
"""Re-encrypt destination secrets under the configured independent current key.

Dry-run by default. Keep the old pepper and decryption keys available until
all active and previous secrets have been migrated. Never prints secrets.
"""
import argparse
import sys
from pathlib import Path
from sqlalchemy import select

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "api"))
from app import models, webhook_crypto  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the re-encrypted ciphertexts")
    args = parser.parse_args()
    keys, current = webhook_crypto.keyring()
    if not keys:
        raise SystemExit("configure independent webhook encryption keys before migration")
    cursor = ""
    count = 0
    while True:
        with SessionLocal() as db:
            rows = db.execute(select(models.WebhookDestination).where(models.WebhookDestination.id > cursor)
                              .order_by(models.WebhookDestination.id).limit(200).with_for_update()).scalars().all()
            if not rows:
                break
            for row in rows:
                for field in ("secret_enc", "previous_secret_enc"):
                    value = getattr(row, field)
                    if not value or value.startswith(f"aesgcm2${current}$"):
                        continue
                    secret = webhook_crypto.decrypt(value, destination_id=row.id)
                    setattr(row, field, webhook_crypto.encrypt(secret, destination_id=row.id))
                    count += 1
            cursor = rows[-1].id
            if args.apply:
                db.commit()
            else:
                db.rollback()
    print(f"{'Migrated' if args.apply else 'Validated dry-run'} {count} encrypted secrets; target key id {current}")


if __name__ == "__main__":
    main()
