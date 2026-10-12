"""Explicit development mode and fail-closed production configuration."""
import os


def validate_environment(env=None):
    env = os.environ if env is None else env
    mode = env.get("HCA_ENV", "production" if env.get("K_SERVICE") else "development")
    if mode not in {"development", "production"}:
        raise RuntimeError("HCA_ENV must be development or production")
    if mode != "production":
        return
    failures = []
    if not env.get("DATABASE_URL", "").startswith("postgresql"):
        failures.append("Postgres DATABASE_URL")
    for name in ("HCA_ADMIN_TOKEN", "SWEEP_TRIGGER_TOKEN", "HCA_API_KEY_PEPPER"):
        if len(env.get(name, "")) < 32:
            failures.append(name + " with at least 32 characters")
    if env.get("HCA_REQUIRE_GOOGLE_LINK") != "1":
        failures.append("HCA_REQUIRE_GOOGLE_LINK=1")
    if not (env.get("HCA_GOOGLE_CLIENT_ID", "").strip() or env.get("HCA_GOOGLE_CLIENT_IDS", "").strip()):
        failures.append("Google OAuth client ids")
    if env.get("HCA_WEBHOOK_ALLOW_INSECURE", "") not in {"", "0"}:
        failures.append("insecure webhooks disabled")
    if not env.get("HCA_WEBHOOK_ENCRYPTION_KEYS") or not env.get("HCA_WEBHOOK_CURRENT_KEY_ID"):
        failures.append("versioned independent webhook encryption keys")
    else:
        from .webhook_crypto import keyring
        try:
            keys, current = keyring(env)
            if not keys or current not in keys:
                raise RuntimeError("empty keyring")
        except RuntimeError:
            failures.append("valid webhook encryption keyring")
    if failures:
        raise RuntimeError("Invalid production configuration: " + "; ".join(failures))
