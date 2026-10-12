#!/usr/bin/env python3
"""Generate disposable local credentials and migrate a local development DB."""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
env_file = root / ".env"
if not env_file.exists():
    values = {
        "HCA_ENV": "development", "DATABASE_URL": "sqlite:///" + str(root / "api/hca_local.db"),
        "HCA_ADMIN_TOKEN": secrets.token_urlsafe(32), "SWEEP_TRIGGER_TOKEN": secrets.token_urlsafe(32),
        "HCA_API_KEY_PEPPER": secrets.token_urlsafe(32), "HCA_REQUIRE_GOOGLE_LINK": "0",
        "HCA_WEBHOOK_ENCRYPTION_KEYS": json.dumps({"local": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}),
        "HCA_WEBHOOK_CURRENT_KEY_ID": "local", "HCA_WEBHOOK_ALLOW_INSECURE": "1",
    }
    env_file.write_text("# Local development only; never commit this file.\n" +
                        "\n".join(f"{k}='{v}'" for k, v in values.items()) + "\n")
    env_file.chmod(0o600)
values = dotenv_values(env_file)
if values.get("HCA_ENV") != "development" or not values.get("DATABASE_URL", "").startswith("sqlite"):
    raise SystemExit("dev_setup refuses a production or non-SQLite environment")
env = os.environ | {k: v for k, v in values.items() if v is not None}
subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root / "api", env=env, check=True)
print("Local database ready; private development configuration saved to .env (values withheld).")
print("Start: cd api && ../.venv/bin/uvicorn app.main:app --env-file ../.env --reload")
