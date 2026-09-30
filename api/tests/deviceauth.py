"""Signed human-surface calls for tests (HCA-P6, app/devicesig.py).

One test device per process: a P-256 key generated here and registered
straight into the DB (the same rows POST /v1/devices writes). human_get
and human_post sign exactly the bytes and path the TestClient will send,
so the server's canonical string matches.
"""

import base64
import json
import os
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app import models
from app.db import SessionLocal
from app.devicesig import canonical_string, device_id_for_public_key_pem

_private = ec.generate_private_key(ec.SECP256R1())
PUBLIC_PEM = _private.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()
DEVICE_ID = device_id_for_public_key_pem(PUBLIC_PEM)
DEVICE_LABEL = "test browser"


def ensure_registered():
    with SessionLocal() as db:
        d = db.get(models.Device, DEVICE_ID)
        if d is None:
            db.add(models.Device(id=DEVICE_ID, label=DEVICE_LABEL, surface="browser_overlay",
                                 public_key_pem=PUBLIC_PEM, created_at=datetime.now(timezone.utc)))
            db.commit()
        elif d.revoked_at is not None:
            d.revoked_at = None
            db.commit()


def raw_signature(private_key, canonical: bytes) -> str:
    der = private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def human_headers(method: str, path_and_query: str, body: bytes = b"", *, private_key=None,
                  device_id: str = None, timestamp: int = None, nonce: str = None) -> dict:
    ensure_registered()
    private_key = private_key or _private
    ts = str(int(time.time()) if timestamp is None else timestamp)
    nonce = nonce or secrets.token_urlsafe(12)
    canonical = canonical_string(ts, nonce, method, path_and_query, body)
    return {
        "X-HCA-Device": device_id or DEVICE_ID,
        "X-HCA-Timestamp": ts,
        "X-HCA-Nonce": nonce,
        "X-HCA-Signature": raw_signature(private_key, canonical),
    }


def _with_query(path: str, params=None) -> str:
    if not params:
        return path
    return f"{path}?{urlencode(params)}"


def human_get(client, path: str, params=None, **kw):
    url = _with_query(path, params)
    headers = dict(kw.pop("headers", {}) or {})
    headers.update(human_headers("GET", url))
    return client.get(url, headers=headers, **kw)


def human_post(client, path: str, json=None, **kw):
    body = b"" if json is None else __import__("json").dumps(json, separators=(",", ":")).encode()
    headers = dict(kw.pop("headers", {}) or {})
    headers["Content-Type"] = "application/json"
    headers.update(human_headers("POST", path, body))
    return client.post(path, content=body, headers=headers, **kw)
