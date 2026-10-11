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


TEST_HOOK_URL = "https://example.test/hook"


def agent_key(caller: str) -> str:
    plaintext = _cache.get(caller)
    with SessionLocal() as db:
        if plaintext and verify_api_key(db, plaintext) is not None:
            register_destination(caller, TEST_HOOK_URL)
            return plaintext
        plaintext = issue_api_key(db, caller)["api_key"]
    _cache[caller] = plaintext
    # HCA-P21a: the suite's asks name TEST_HOOK_URL; register it for the
    # caller so they pass decision 7's 400 without touching every test.
    register_destination(caller, TEST_HOOK_URL)
    return plaintext


def agent_auth(caller: str) -> dict:
    return {"Authorization": f"Bearer {agent_key(caller)}"}


def register_destination(caller: str, url: str) -> str:
    """HCA-P21a: asks may only name a registered destination (400 otherwise).
    Registers url for caller in the default tenant if it is not active yet
    and returns the destination id. Lives beside agent_key because every
    test that posts a webhook ask needs both."""
    from app import webhook_destinations as registry
    from app import models
    with SessionLocal() as db:
        existing = registry.find_active_by_url(db, models.DEFAULT_TENANT_ID, caller, url)
        if existing is not None:
            return existing.id
        dest, _secret = registry.register(db, tenant_id=models.DEFAULT_TENANT_ID, caller=caller, url=url)
        return dest.id
