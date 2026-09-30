"""Caller authentication for the agent-facing routes.

Plan section 4 (Auth and Cryptography Model): automations authenticate
with per-integration API keys, hca_live_<prefix>_<secret>, hashed at
rest. HCA-P3 built issuance and verification (app/apikeys.py) and
enforced nothing; every route was open. This module closes that.

Two guards:

  require_agent_key: Authorization: Bearer hca_live_... on the routes an
    automation calls (POST /v1/requests, GET /v1/requests/{id},
    POST /v1/requests/{id}/cancel). 401 when the header is missing or not
    a bearer; 403 when the key does not verify or is revoked. Returns the
    ApiKey row so the route can bind the ask to the key's caller: a key
    creates asks only as its own caller (403 otherwise) and can read or
    cancel only its own asks (404 for another caller's ask, so ids cannot
    be enumerated across callers).

  require_admin_bearer: Authorization: Bearer <HCA_ADMIN_TOKEN> on
    POST /v1/api-keys. Minting a key for any caller was open, which made
    enforcement pointless; the owner mints keys with a single admin token
    read from the environment (Secret Manager at deploy time, same pattern
    as SWEEP_TRIGGER_TOKEN). Missing configuration fails closed (403).

  require_device: the human surface routes (GET /v1/inbox,
    GET /v1/inbox/stream, POST /v1/requests/{id}/answer, GET /v1/audit)
    carry the X-HCA-Device, X-HCA-Timestamp, X-HCA-Nonce and
    X-HCA-Signature headers described in app/devicesig.py. 401 when a
    header is missing, the timestamp is stale, the nonce was already used
    or the signature does not verify; 403 when the device is unknown or
    revoked. Returns the Device row so the answer route can record which
    device answered. HCA-P6 closed these routes; Google sign-in on top of
    the device key is the next packet.
"""

import hmac
import os
from typing import Optional

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .apikeys import verify_api_key
from .db import get_db
from . import devicesig


# auto_error=False so a missing header is our own 401 (with WWW-Authenticate)
# rather than FastAPI's default 403. scheme_name matches the securitySchemes
# names in api/openapi.yaml so the generated schema and the spec agree.
_agent_bearer = HTTPBearer(auto_error=False, scheme_name="AgentKey")
_admin_bearer = HTTPBearer(auto_error=False, scheme_name="AdminToken")
# Declares the device scheme in the generated OpenAPI (securitySchemes
# DeviceSignature, matching api/openapi.yaml); the real check reads all four
# X-HCA-* headers from the request in require_device.
_device_signature = APIKeyHeader(name="X-HCA-Signature", auto_error=False, scheme_name="DeviceSignature",
                                 description="See app/devicesig.py: X-HCA-Device, X-HCA-Timestamp, X-HCA-Nonce and X-HCA-Signature over the canonical request string")


def _token(creds: Optional[HTTPAuthorizationCredentials]) -> str:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials.strip():
        raise HTTPException(status_code=401, detail="missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    return creds.credentials.strip()


def require_agent_key(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_agent_bearer),
    db: Session = Depends(get_db),
) -> models.ApiKey:
    token = _token(creds)
    key = verify_api_key(db, token)
    if key is None:
        raise HTTPException(status_code=403, detail="invalid or revoked api key")
    return key


def require_admin_bearer(creds: Optional[HTTPAuthorizationCredentials] = Depends(_admin_bearer)) -> None:
    expected = os.environ.get("HCA_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=403, detail="admin token is not configured")
    token = _token(creds)
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="invalid admin token")


def _device_401(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "HCA-Device"})


async def require_device(request: Request, db: Session = Depends(get_db), _sig: Optional[str] = Depends(_device_signature)) -> models.Device:
    h = request.headers
    device_id = h.get(devicesig.HEADER_DEVICE, "").strip()
    timestamp = h.get(devicesig.HEADER_TIMESTAMP, "").strip()
    nonce = h.get(devicesig.HEADER_NONCE, "").strip()
    signature = h.get(devicesig.HEADER_SIGNATURE, "").strip()
    if not (device_id and timestamp and nonce and signature):
        raise _device_401("missing device signature headers")
    if not devicesig.timestamp_is_fresh(timestamp):
        raise _device_401("stale device signature timestamp")

    device = db.get(models.Device, device_id)
    if device is None:
        raise HTTPException(status_code=403, detail="unknown device")
    if device.revoked_at is not None:
        raise HTTPException(status_code=403, detail="device revoked")

    body = await request.body()
    path_and_query = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    canonical = devicesig.canonical_string(timestamp, nonce, request.method, path_and_query, body)
    if not devicesig.verify_signature(device.public_key_pem, canonical, signature):
        raise _device_401("device signature does not verify")
    if not devicesig.replay_guard.check_and_record(device_id, nonce):
        raise _device_401("device signature replayed")

    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return device
