"""Versioned independent AES-GCM keys with explicit legacy-reading overlap.

The optional keyring changes new ciphertext writes to aesgcm2. Legacy aesgcm
values remain readable under the current API-key pepper and up to three
explicit previous peppers. Rewrap both destination columns before retiring
any encryption key or pepper; this module never rotates data automatically.
"""
import base64
import json
import os
import re
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SCHEME = "aesgcm"
_INFO = b"hca-webhook-secret-v1"
MAX_KEYRING_KEYS = 10


class SecretUnreadable(ValueError):
    """A ciphertext is unavailable, unauthenticated, or uses a missing key."""


def _pepper() -> bytes:
    value = os.environ.get("HCA_API_KEY_PEPPER", "")
    if len(value) < 16:
        raise RuntimeError("HCA_API_KEY_PEPPER is unset or shorter than 16 characters")
    return value.encode("utf-8")


def _derive(pepper: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(pepper)


def _key() -> bytes:
    return _derive(_pepper())


def _unique_keys(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate key id")
        values[key] = value
    return values


def keyring(env=None):
    """Validate at most ten URL-safe base64 keys of exactly 32 bytes.

    Absent configuration retains legacy writes. A configured nonempty ring
    requires HCA_WEBHOOK_CURRENT_KEY_ID to name its new-write key.
    """
    env = os.environ if env is None else env
    try:
        raw = env.get("HCA_WEBHOOK_ENCRYPTION_KEYS", "{}")
        if not isinstance(raw, str) or len(raw) > 16384:
            raise ValueError()
        values = json.loads(raw, object_pairs_hook=_unique_keys)
        if not isinstance(values, dict) or len(values) > MAX_KEYRING_KEYS:
            raise ValueError()
        keys = {}
        for key_id, encoded in values.items():
            if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", key_id):
                raise ValueError()
            if not isinstance(encoded, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}=?", encoded):
                raise ValueError()
            key = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            if len(key) != 32 or base64.urlsafe_b64encode(key).decode().rstrip("=") != encoded.rstrip("="):
                raise ValueError()
            keys[key_id] = key
        current = env.get("HCA_WEBHOOK_CURRENT_KEY_ID", "")
        if (keys and current not in keys) or (not keys and current):
            raise ValueError()
        return keys, current
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("invalid webhook encryption keyring configuration") from None


def encrypt(secret: str, *, destination_id: str) -> str:
    keys, current = keyring()
    nonce = secrets.token_bytes(12)
    key = keys[current] if keys else _key()
    ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), destination_id.encode("utf-8"))
    if keys:
        return f"aesgcm2${current}${nonce.hex()}${ciphertext.hex()}"
    return f"{SCHEME}${nonce.hex()}${ciphertext.hex()}"


def _cipher_parts(nonce_hex, ciphertext_hex):
    if not re.fullmatch(r"[0-9a-f]{24}", nonce_hex) or not re.fullmatch(r"[0-9a-f]{32,8192}", ciphertext_hex):
        raise ValueError("malformed ciphertext")
    return bytes.fromhex(nonce_hex), bytes.fromhex(ciphertext_hex)


def decrypt(stored: str, *, destination_id: str) -> str:
    try:
        if not isinstance(stored, str) or len(stored) > 8192:
            raise ValueError("missing ciphertext")
        aad = destination_id.encode("utf-8")
        if stored.startswith("aesgcm2$"):
            _, key_id, nonce_hex, ciphertext_hex = stored.split("$", 3)
            keys, _ = keyring()
            nonce, ciphertext = _cipher_parts(nonce_hex, ciphertext_hex)
            return AESGCM(keys[key_id]).decrypt(nonce, ciphertext, aad).decode("utf-8")
        if stored.startswith(SCHEME + "$"):
            from .apikeys import previous_peppers
            _, nonce_hex, ciphertext_hex = stored.split("$", 2)
            nonce, ciphertext = _cipher_parts(nonce_hex, ciphertext_hex)
            peppers = [_pepper(), *(pepper.encode("utf-8") for pepper in previous_peppers())]
            for pepper in peppers:
                try:
                    return AESGCM(_derive(pepper)).decrypt(nonce, ciphertext, aad).decode("utf-8")
                except Exception:
                    continue
        raise ValueError("no decryptable signing secret")
    except Exception as exc:
        raise SecretUnreadable(f"secret does not decrypt: {type(exc).__name__}") from None
