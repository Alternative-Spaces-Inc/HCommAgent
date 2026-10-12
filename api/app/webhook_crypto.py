"""HCA-P21a Day 2 (decision 8): webhook signing secrets at rest.

A delivery is signed with HMAC-SHA256 under the destination secret, so the
server must be able to read the secret back; a one way hash (Day 1) cannot
sign. The secret is therefore stored encrypted with AES-256-GCM under a key
derived by HKDF-SHA256 from the api key pepper (HCA_API_KEY_PEPPER, mounted
from Secret Manager) with a fixed info string. No new credential exists; a
dumped table is useless without the pepper, and rotating the pepper makes
every stored secret unreadable, which is why the pepper rotation runbook
must re-register destinations (noted in docs/plans).

Format: "aesgcm$<nonce hex 24>$<ciphertext hex>"; the destination id is the
associated data so a ciphertext moved to another row fails to decrypt.
"""

import os
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


def encrypt(secret: str, *, destination_id: str) -> str:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_key()).encrypt(nonce, secret.encode("utf-8"), destination_id.encode("utf-8"))
    return f"{SCHEME}${nonce.hex()}${ct.hex()}"


def decrypt(stored: str, *, destination_id: str) -> str:
    if not stored or not stored.startswith(SCHEME + "$"):
        raise SecretUnreadable("no encrypted secret on the destination")
    try:
        _, nonce_hex, ct_hex = stored.split("$", 2)
        pt = AESGCM(_key()).decrypt(bytes.fromhex(nonce_hex), bytes.fromhex(ct_hex), destination_id.encode("utf-8"))
    except Exception as exc:  # InvalidTag, ValueError
        raise SecretUnreadable(f"secret does not decrypt: {type(exc).__name__}") from None
    return pt.decode("utf-8")
