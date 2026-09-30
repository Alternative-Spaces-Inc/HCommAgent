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

Not guarded here, on purpose: the human surface routes (GET /v1/inbox,
GET /v1/inbox/stream, POST /v1/requests/{id}/answer) and GET /v1/audit.
Those take the plan's Google OAuth sign-in and ECDSA device signing, a
later packet, so an unauthenticated overlay keeps working meanwhile.
"""

import hmac
import os
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .apikeys import verify_api_key
from .db import get_db


# auto_error=False so a missing header is our own 401 (with WWW-Authenticate)
# rather than FastAPI's default 403. scheme_name matches the securitySchemes
# names in api/openapi.yaml so the generated schema and the spec agree.
_agent_bearer = HTTPBearer(auto_error=False, scheme_name="AgentKey")
_admin_bearer = HTTPBearer(auto_error=False, scheme_name="AdminToken")


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
