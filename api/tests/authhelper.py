"""Bearer headers for the agent-facing routes in tests.

Since HCA-P4 every agent route needs Authorization: Bearer hca_live_...
bound to the ask's caller. agent_auth(caller) mints (once per caller,
re-minting only if the DB was reset under it) through app.apikeys, the
same code path POST /v1/api-keys uses, and returns the header dict.
ADMIN_AUTH is the header for POST /v1/api-keys itself; conftest sets the
matching HCA_ADMIN_TOKEN for the whole suite.
"""

import os

from app.db import SessionLocal
from app.apikeys import issue_api_key, verify_api_key

_cache: dict[str, str] = {}

ADMIN_TOKEN = os.environ.setdefault("HCA_ADMIN_TOKEN", "test-admin-token")
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def agent_key(caller: str) -> str:
    plaintext = _cache.get(caller)
    with SessionLocal() as db:
        if plaintext and verify_api_key(db, plaintext) is not None:
            return plaintext
        plaintext = issue_api_key(db, caller)["api_key"]
    _cache[caller] = plaintext
    return plaintext


def agent_auth(caller: str) -> dict:
    return {"Authorization": f"Bearer {agent_key(caller)}"}
