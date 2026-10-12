"""Per-device request signing for the human surface routes (plan section 4,
Proof of Surface). HCA-P6.

The browser overlay holds an ECDSA P-256 key in extension storage. The
owner registers its public key once (POST /v1/devices, admin bearer). From
then on every call to a human route carries three headers:

  X-HCA-Device:    the device id, dev_ + the first 32 hex chars of
                   SHA-256 over the SubjectPublicKeyInfo DER of the key
  X-HCA-Timestamp: unix seconds when the request was signed
  X-HCA-Nonce:     random token, unique per request
  X-HCA-Signature: base64url, no padding, of the raw 64-byte r||s
                   ECDSA signature WebCrypto produces

over the canonical string

  "HCA1\n" + timestamp + "\n" + nonce + "\n" + METHOD + "\n" + path_and_query + "\n" + sha256hex(body)

where METHOD is upper case, path_and_query is the request path plus "?" and
the raw query string when present, and body is the exact bytes sent (empty
for GET, so sha256hex(b"")).

Verification rejects a timestamp more than MAX_SKEW_SECONDS from the
server clock and a (device, nonce) pair seen inside that window, so a
captured request cannot be replayed. The replay set is in process; the
service runs with max instances 1 (see cloudbuild.yaml), which is what
makes that sufficient for now.
"""

import base64
import hashlib
import threading
import time
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

MAX_SKEW_SECONDS = 300
NONCE_RETENTION_SECONDS = 2 * MAX_SKEW_SECONDS + 1
PREFIX = "HCA1"

HEADER_DEVICE = "x-hca-device"
HEADER_TIMESTAMP = "x-hca-timestamp"
HEADER_NONCE = "x-hca-nonce"
HEADER_SIGNATURE = "x-hca-signature"


def device_id_for_public_key_pem(pem: str) -> str:
    key = load_public_key(pem)
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return "dev_" + hashlib.sha256(der).hexdigest()[:32]


def load_public_key(pem: str) -> ec.EllipticCurvePublicKey:
    key = serialization.load_pem_public_key(pem.encode("utf-8"))
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("public key must be ECDSA P-256")
    return key


def canonical_string(timestamp: str, nonce: str, method: str, path_and_query: str, body: bytes) -> bytes:
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return "\n".join([PREFIX, timestamp, nonce, method.upper(), path_and_query, body_hash]).encode("utf-8")


def _b64url_decode(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_signature(pem: str, canonical: bytes, signature_b64url: str) -> bool:
    try:
        raw = _b64url_decode(signature_b64url)
    except Exception:
        return False
    if len(raw) != 64:
        return False
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    try:
        load_public_key(pem).verify(encode_dss_signature(r, s), canonical, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


class ReplayGuard:
    """Remembers (device, nonce) pairs for the skew window."""

    def __init__(self, ttl_seconds: int = NONCE_RETENTION_SECONDS):
        self._ttl = ttl_seconds
        self._seen: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def check_and_record(self, device_id: str, nonce: str, now: Optional[float] = None) -> bool:
        """True when the pair is new (and now recorded); False on a replay."""
        now = time.time() if now is None else now
        key = (device_id, nonce)
        with self._lock:
            cutoff = now - self._ttl
            if len(self._seen) > 4096:
                for k, t in list(self._seen.items()):
                    if t < cutoff:
                        del self._seen[k]
            t = self._seen.get(key)
            if t is not None and t >= cutoff:
                return False
            self._seen[key] = now
            return True

    def reset(self):
        with self._lock:
            self._seen.clear()


replay_guard = ReplayGuard()


def timestamp_is_fresh(timestamp: str, now: Optional[float] = None) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = time.time() if now is None else now
    return abs(now - ts) <= MAX_SKEW_SECONDS
