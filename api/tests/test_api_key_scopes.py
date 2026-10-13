"""HCA-P21b Day 1: api key scopes, scoped routes, PATCH and lifecycle events.

Decision 9 (docs/decisions/DECISIONS_20261012.md): four fixed scopes, all
four by default, 403 names the missing scope, pre-P21b rows are backfilled
with every scope by the migration's server default.
"""
import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import SessionLocal
from app import models
from app.apikeys import SCOPES, normalize_scopes
from authhelper import ADMIN_AUTH


client = TestClient(app)
ALL = list(SCOPES)


def _issue(caller, scopes=None):
    body = {"caller": caller}
    if scopes is not None:
        body["scopes"] = scopes
    resp = client.post("/v1/api-keys", json=body, headers=ADMIN_AUTH)
    assert resp.status_code == 201, resp.text
    issued = resp.json()
    return issued, {"Authorization": f"Bearer {issued['api_key']}"}


def _ask_body():
    return {"question": "Scope proof?", "kind": "approve_deny", "deliver_to": {"surface": "browser_overlay"}}


def test_normalize_scopes_vocabulary():
    assert normalize_scopes(None) == ALL
    assert normalize_scopes(["webhooks:manage", "asks:create"]) == ["asks:create", "webhooks:manage"]
    for bad in ([], ["asks:*"], ["asks:create", "asks:create"], [1], "asks:create"):
        with pytest.raises(ValueError):
            normalize_scopes(bad)


def test_issue_without_scopes_carries_all_four_and_records_issued_event():
    issued, _ = _issue("scopes-default")
    assert issued["scopes"] == ALL
    listed = [k for k in client.get("/v1/api-keys", headers=ADMIN_AUTH).json() if k["prefix"] == issued["prefix"]]
    assert listed and listed[0]["scopes"] == ALL
    events = client.get(f"/v1/api-keys/{issued['prefix']}/events", headers=ADMIN_AUTH)
    assert events.status_code == 200
    assert [e["action"] for e in events.json()] == ["issued"]
    assert events.json()[0]["actor"] == "admin" and events.json()[0]["detail"]["scopes"] == ALL


def test_issue_with_invalid_scopes_is_422():
    for bad in (["asks:*"], ["asks:create", "asks:create"], []):
        resp = client.post("/v1/api-keys", json={"caller": "scopes-bad", "scopes": bad}, headers=ADMIN_AUTH)
        assert resp.status_code == 422, (bad, resp.text)


@pytest.mark.parametrize("scope,method,path,body", [
    ("asks:create", "post", "/v1/requests", _ask_body()),
    ("asks:read", "get", "/v1/requests/does-not-exist", None),
    ("asks:cancel", "post", "/v1/requests/does-not-exist/cancel", {"reason": "proof"}),
    ("webhooks:manage", "post", "/v1/webhooks", {"url": "https://example.com/hook"}),
    ("webhooks:manage", "get", "/v1/webhooks", None),
    ("webhooks:manage", "post", "/v1/webhooks/does-not-exist/rotate", None),
    ("webhooks:manage", "delete", "/v1/webhooks/does-not-exist", None),
])
def test_route_refuses_a_key_lacking_its_scope_and_accepts_one_that_has_it(scope, method, path, body):
    others = [s for s in ALL if s != scope]
    _, lacking = _issue(f"scopes-lacking-{scope}-{method}", others)
    _, having = _issue(f"scopes-having-{scope}-{method}", [scope])

    refused = client.request(method.upper(), path, json=body, headers=lacking)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == f"api key lacks scope {scope}"
    assert refused.headers["WWW-Authenticate"] == f'Bearer scope="{scope}"'

    allowed = client.request(method.upper(), path, json=body, headers=having)
    assert allowed.status_code != 403, allowed.text
    assert allowed.status_code in (200, 201, 404, 400, 409, 422)


def test_patch_scopes_changes_the_list_and_records_an_event():
    issued, agent = _issue("scopes-patch", ["asks:create"])
    prefix = issued["prefix"]
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 403

    assert client.patch(f"/v1/api-keys/{prefix}", json={"scopes": ["asks:read"]}).status_code == 401
    patched = client.patch(f"/v1/api-keys/{prefix}", json={"scopes": ["asks:read"]}, headers=ADMIN_AUTH)
    assert patched.status_code == 200 and patched.json()["scopes"] == ["asks:read"]
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 404
    assert client.post("/v1/requests", json=_ask_body(), headers=agent).status_code == 403

    assert client.patch(f"/v1/api-keys/{prefix}", json={"scopes": ["nope"]}, headers=ADMIN_AUTH).status_code == 422
    assert client.patch("/v1/api-keys/nosuchprefix", json={"scopes": ["asks:read"]}, headers=ADMIN_AUTH).status_code == 404

    # An unchanged list writes no event.
    same = client.patch(f"/v1/api-keys/{prefix}", json={"scopes": ["asks:read"]}, headers=ADMIN_AUTH)
    assert same.status_code == 200
    events = client.get(f"/v1/api-keys/{prefix}/events", headers=ADMIN_AUTH).json()
    assert [e["action"] for e in events] == ["scopes_changed", "issued"]
    assert events[0]["detail"] == {"before": ["asks:create"], "after": ["asks:read"]}


def test_revoke_records_an_event_and_patch_on_a_revoked_key_is_409():
    issued, _ = _issue("scopes-revoke")
    prefix = issued["prefix"]
    assert client.post(f"/v1/api-keys/{prefix}/revoke", headers=ADMIN_AUTH).status_code == 200
    assert client.post(f"/v1/api-keys/{prefix}/revoke", headers=ADMIN_AUTH).status_code == 200
    assert client.patch(f"/v1/api-keys/{prefix}", json={"scopes": ["asks:read"]}, headers=ADMIN_AUTH).status_code == 409
    events = client.get(f"/v1/api-keys/{prefix}/events", headers=ADMIN_AUTH).json()
    assert [e["action"] for e in events] == ["revoked", "issued"]
    assert client.get("/v1/api-keys/nosuchprefix/events", headers=ADMIN_AUTH).status_code == 404
    assert client.get(f"/v1/api-keys/{prefix}/events").status_code == 401

    db = SessionLocal()
    try:
        row = db.execute(select(models.ApiKey).where(models.ApiKey.key_prefix == prefix)).scalar_one()
        stored = db.execute(select(models.ApiKeyEvent).where(models.ApiKeyEvent.api_key_id == row.id)).scalars().all()
        assert {e.action for e in stored} == {"issued", "revoked"}
        assert all(e.tenant_id == row.tenant_id for e in stored)
    finally:
        db.close()


def test_migration_backfills_existing_rows_with_every_scope(tmp_path):
    """Upgrade a throwaway SQLite database to the revision before the scopes
    migration, insert a pre-P21b key row, upgrade to head, and read the
    backfilled scopes column plus the new events table."""
    api_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = tmp_path / "scopes_backfill.db"
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    def alembic(*args):
        run = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=api_dir, env=env,
                             capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        return run.stdout

    alembic("upgrade", "c9d0e1f2a3b4-1")
    con = sqlite3.connect(db_path)
    columns = [c[1] for c in con.execute("PRAGMA table_info(api_keys)")]
    assert "scopes" not in columns
    con.execute("INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES ('default', 'default', '2026-01-01 00:00:00')")
    con.execute("INSERT INTO api_keys (id, caller, tenant_id, key_prefix, key_hash, created_at) "
                "VALUES ('legacy-1', 'legacy-caller', 'default', 'abcdef012345', 'hmac256$00', '2026-01-01 00:00:00')")
    con.commit()
    con.close()

    alembic("upgrade", "head")
    con = sqlite3.connect(db_path)
    scopes = con.execute("SELECT scopes FROM api_keys WHERE id = 'legacy-1'").fetchone()[0]
    assert json.loads(scopes) == ALL
    assert con.execute("SELECT count(*) FROM api_key_events").fetchone()[0] == 0
    indexes = [r[1] for r in con.execute("PRAGMA index_list(api_key_events)")]
    assert "ix_api_key_events_key_at" in indexes

    # Day 2 (d1e2f3a4b5c6): per key ceilings are NULL (deployment default) and the
    # tenant daily cap is 0 (unlimited) on pre-existing rows.
    assert con.execute("SELECT rate_per_minute, asks_per_minute FROM api_keys WHERE id = 'legacy-1'").fetchone() == (None, None)
    assert con.execute("SELECT asks_per_day FROM tenants WHERE id = 'default'").fetchone() == (0,)
    con.close()

    alembic("downgrade", "20261012_rate_limits")
    con = sqlite3.connect(db_path)
    assert "scopes" not in [c[1] for c in con.execute("PRAGMA table_info(api_keys)")]
    assert "rate_per_minute" not in [c[1] for c in con.execute("PRAGMA table_info(api_keys)")]
    assert "asks_per_day" not in [c[1] for c in con.execute("PRAGMA table_info(tenants)")]
    assert con.execute("SELECT name FROM sqlite_master WHERE name = 'api_key_events'").fetchone() is None
    con.close()
