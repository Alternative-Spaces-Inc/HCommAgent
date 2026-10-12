"""Versioned independent AES-GCM keys; legacy pepper-derived decrypt is migration-only."""

import os
import json
import base64
import re
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SCHEME = "aesgcm"
_INFO = b"hca-webhook-secret-v1"


class SecretUnreadable(ValueError):
    """The stored value cannot be decrypted (wrong pepper, moved row, or a
    Day 1 row that holds a hash only)."""


def _pepper() -> bytes:
    value = os.environ.get("HCA_API_KEY_PEPPER", "")
    if len(value) < 16:
        raise RuntimeError("HCA_API_KEY_PEPPER is unset or shorter than 16 characters")
    return value.encode("utf-8")


def _key() -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(_pepper())


def keyring(env=None):
    env = os.environ if env is None else env
    raw = env.get("HCA_WEBHOOK_ENCRYPTION_KEYS", "{}")
    try:
        values = json.loads(raw)
        if not isinstance(values, dict) or len(values) > 10:
            raise ValueError()
        keys = {}
        for key_id, encoded in values.items():
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", key_id):
                raise ValueError()
            key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if len(key) != 32:
                raise ValueError()
            keys[key_id] = key
        current = env.get("HCA_WEBHOOK_CURRENT_KEY_ID", "")
        if keys and current not in keys:
            raise ValueError()
        return keys, current
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("invalid webhook encryption keyring configuration") from None


def encrypt(secret: str, *, destination_id: str) -> str:
    keys, current = keyring()
    if not keys and os.environ.get("HCA_ENV") == "production":
        raise RuntimeError("production requires independent webhook encryption keys")
    nonce = secrets.token_bytes(12)
    key = keys[current] if keys else _key()
    ct = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), destination_id.encode("utf-8"))
    return f"aesgcm2${current}${nonce.hex()}${ct.hex()}" if keys else f"{SCHEME}${nonce.hex()}${ct.hex()}"


def decrypt(stored: str, *, destination_id: str) -> str:
    try:
        if stored.startswith("aesgcm2$"):
            _, key_id, nonce_hex, ct_hex = stored.split("$", 3)
            keys, _ = keyring()
            key = keys[key_id]
            pt = AESGCM(key).decrypt(bytes.fromhex(nonce_hex), bytes.fromhex(ct_hex), destination_id.encode("utf-8"))
        elif stored.startswith(SCHEME + "$"):
            _, nonce_hex, ct_hex = stored.split("$", 2)
            pt = AESGCM(_key()).decrypt(bytes.fromhex(nonce_hex), bytes.fromhex(ct_hex), destination_id.encode("utf-8"))
        else:
            raise ValueError("no encrypted signing secret")
        return pt.decode("utf-8")
    except Exception as exc:
        raise SecretUnreadable(f"secret does not decrypt: {type(exc).__name__}") from None
