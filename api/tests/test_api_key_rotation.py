"""HCA-P21b Day 3: api key rotation with a 24 h overlap.

Decision 9 (docs/decisions/DECISIONS_20261012.md): admin rotate and agent
self rotate; the old key stays valid until previous_valid_until and then
counts as expired; the unique caller constraint becomes a partial unique
index so the rotating and the new key coexist.
"""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.main import app
from app.db import SessionLocal
from app import models
from app.apikeys import SCOPES
from authhelper import ADMIN_AUTH


client = TestClient(app)


def _issue(caller, scopes=None, **extra):
    body = {"caller": caller, **extra}
    if scopes is not None:
        body["scopes"] = scopes
    resp = client.post("/v1/api-keys", json=body, headers=ADMIN_AUTH)
    assert resp.status_code == 201, resp.text
    issued = resp.json()
    return issued, {"Authorization": f"Bearer {issued['api_key']}"}


def _ask_body(caller):
    return {"caller": caller, "audience": "owner", "title": "Rotation proof", "control": "chat",
            "options_or_fields": [], "suggested": {}, "deliver_to": {"mode": "poll"}}


def _post_ask(auth, caller):
    return client.post("/v1/requests", json=_ask_body(caller), headers=auth)


def _listed(prefix):
    rows = [k for k in client.get("/v1/api-keys", headers=ADMIN_AUTH).json() if k["prefix"] == prefix]
    assert rows, prefix
    return rows[0]


def _events(prefix):
    return [e["action"] for e in client.get(f"/v1/api-keys/{prefix}/events", headers=ADMIN_AUTH).json()]


def _set_valid_until(prefix, when):
    with SessionLocal() as db:
        db.execute(update(models.ApiKey).where(models.ApiKey.key_prefix == prefix).values(previous_valid_until=when))
        db.commit()


def test_admin_rotate_keeps_scopes_and_ceilings_and_old_key_works_in_overlap():
    issued, old_auth = _issue("rot-admin", scopes=["asks:create", "asks:read"], rate_per_minute=120, asks_per_minute=12)
    resp = client.post(f"/v1/api-keys/{issued['prefix']}/rotate", headers=ADMIN_AUTH)
    assert resp.status_code == 201, resp.text
    rotated = resp.json()
    assert rotated["caller"] == "rot-admin" and rotated["prefix"] != issued["prefix"]
    assert rotated["scopes"] == ["asks:create", "asks:read"]
    assert rotated["rate_per_minute"] == 120 and rotated["asks_per_minute"] == 12
    assert rotated["previous_prefix"] == issued["prefix"]
    until = datetime.fromisoformat(rotated["previous_valid_until"].replace("Z", "+00:00"))
    assert timedelta(hours=23) < until - datetime.now(timezone.utc) <= timedelta(hours=24)
    new_auth = {"Authorization": f"Bearer {rotated['api_key']}"}

    old_ask = _post_ask(old_auth, "rot-admin")
    assert old_ask.status_code == 201, old_ask.text
    assert _post_ask(new_auth, "rot-admin").status_code == 201

    old_view, new_view = _listed(issued["prefix"]), _listed(rotated["prefix"])
    assert old_view["state"] == "rotating" and old_view["superseded_by"] == rotated["prefix"]
    assert old_view["previous_valid_until"] is not None and old_view["revoked_at"] is None
    assert new_view["state"] == "active" and new_view["superseded_by"] is None

    assert _events(issued["prefix"]) == ["rotated", "issued"]
    assert _events(rotated["prefix"]) == ["issued"]
    detail = client.get(f"/v1/api-keys/{rotated['prefix']}/events", headers=ADMIN_AUTH).json()[0]["detail"]
    assert detail["rotated_from"] == issued["prefix"]


def test_old_key_is_refused_after_overlap_and_reads_expired():
    issued, old_auth = _issue("rot-expiry")
    rotated = client.post(f"/v1/api-keys/{issued['prefix']}/rotate", headers=ADMIN_AUTH).json()
    _set_valid_until(issued["prefix"], datetime.now(timezone.utc) - timedelta(seconds=1))

    refused = _post_ask(old_auth, "rot-expiry")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "invalid or revoked api key"
    view = _listed(issued["prefix"])
    assert view["state"] == "expired" and view["revoked_at"] is not None
    assert _events(issued["prefix"])[0] == "expired"
    # The replacement is untouched.
    assert _post_ask({"Authorization": f"Bearer {rotated['api_key']}"}, "rot-expiry").status_code == 201
    assert _listed(rotated["prefix"])["state"] == "active"


def test_rotating_again_revoked_or_unknown_is_refused():
    issued, _ = _issue("rot-twice")
    first = client.post(f"/v1/api-keys/{issued['prefix']}/rotate", headers=ADMIN_AUTH)
    assert first.status_code == 201
    second = client.post(f"/v1/api-keys/{issued['prefix']}/rotate", headers=ADMIN_AUTH)
    assert second.status_code == 409 and second.json()["detail"] == "api key already rotated"

    revoked, _ = _issue("rot-revoked")
    assert client.post(f"/v1/api-keys/{revoked['prefix']}/revoke", headers=ADMIN_AUTH).status_code == 200
    resp = client.post(f"/v1/api-keys/{revoked['prefix']}/rotate", headers=ADMIN_AUTH)
    assert resp.status_code == 409 and resp.json()["detail"] == "api key is revoked"

    assert client.post("/v1/api-keys/doesnotexist00/rotate", headers=ADMIN_AUTH).status_code == 404


def test_self_rotate_under_any_scope_and_not_twice():
    issued, auth = _issue("rot-self", scopes=["asks:read"])
    resp = client.post("/v1/api-keys/self/rotate", headers=auth)
    assert resp.status_code == 201, resp.text
    rotated = resp.json()
    assert rotated["caller"] == "rot-self" and rotated["scopes"] == ["asks:read"]
    assert rotated["previous_prefix"] == issued["prefix"]
    # The old key still authenticates during the overlap but cannot chain a second rotation.
    again = client.post("/v1/api-keys/self/rotate", headers=auth)
    assert again.status_code == 409 and again.json()["detail"] == "api key already rotated"
    # The new key can rotate itself later.
    new_auth = {"Authorization": f"Bearer {rotated['api_key']}"}
    assert client.post("/v1/api-keys/self/rotate", headers=new_auth).status_code == 201
    actor = client.get(f"/v1/api-keys/{issued['prefix']}/events", headers=ADMIN_AUTH).json()[0]["actor"]
    assert actor == f"key:{issued['prefix']}"


def test_self_rotate_requires_an_agent_key():
    assert client.post("/v1/api-keys/self/rotate").status_code == 401
    assert client.post("/v1/api-keys/self/rotate", headers={"Authorization": "Bearer hca_live_bad_bad"}).status_code == 403
    assert client.post("/v1/api-keys/self/rotate", headers=ADMIN_AUTH).status_code == 403


def test_reissue_after_rotation_replaces_only_the_active_row():
    issued, _ = _issue("rot-reissue")
    rotated = client.post(f"/v1/api-keys/{issued['prefix']}/rotate", headers=ADMIN_AUTH).json()
    reissued, _ = _issue("rot-reissue", scopes=["asks:cancel"])
    assert reissued["prefix"] != rotated["prefix"]
    with SessionLocal() as db:
        rows = db.execute(select(models.ApiKey).where(models.ApiKey.caller == "rot-reissue")).scalars().all()
    assert len(rows) == 2
    states = sorted(r.state for r in rows)
    assert states == ["active", "rotating"]
    assert _listed(reissued["prefix"])["scopes"] == ["asks:cancel"]
    # The rotated prefix was replaced in place, so the old row still points at it by id.
    assert _listed(issued["prefix"])["superseded_by"] == reissued["prefix"]


def test_partial_unique_index_allows_history_but_one_active_key_per_caller():
    with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        a = models.ApiKey(id="rot-ix-a", caller="rot-index", key_prefix="rotixa00000a", key_hash="hmac256$00", created_at=now,
                          superseded_by="rot-ix-b", previous_valid_until=now)
        b = models.ApiKey(id="rot-ix-b", caller="rot-index", key_prefix="rotixb00000b", key_hash="hmac256$00", created_at=now)
        c = models.ApiKey(id="rot-ix-c", caller="rot-index", key_prefix="rotixc00000c", key_hash="hmac256$00", created_at=now, revoked_at=now)
        db.add_all([a, b, c])
        db.commit()
        d = models.ApiKey(id="rot-ix-d", caller="rot-index", key_prefix="rotixd00000d", key_hash="hmac256$00", created_at=now)
        db.add(d)
        failed = False
        try:
            db.commit()
        except Exception:
            failed = True
            db.rollback()
        assert failed, "a second active key for the same caller must violate ux_api_keys_caller_active"
        for row in db.execute(select(models.ApiKey).where(models.ApiKey.caller == "rot-index")).scalars().all():
            db.delete(row)
        db.commit()


def test_migration_replaces_caller_unique_with_partial_index(tmp_path):
    """Upgrade a throwaway SQLite database to Day 2, insert a key, upgrade to
    head: the plain unique constraint is gone (a superseded sibling row is
    accepted), the partial index refuses a second active row, and the new
    columns exist. Then downgrade one step and check the constraint is back."""
    api_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = tmp_path / "rotation.db"
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    def alembic(*args):
        run = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=api_dir, env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        return run.stdout

    alembic("upgrade", "d1e2f3a4b5c6")
    con = sqlite3.connect(db_path)
    con.execute("INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES ('default', 'default', '2026-01-01 00:00:00')")
    con.execute("INSERT INTO api_keys (id, caller, tenant_id, key_prefix, key_hash, created_at) "
                "VALUES ('k1', 'caller-a', 'default', 'aaaaaaaaaaaa', 'hmac256$00', '2026-01-01 00:00:00')")
    con.commit()
    try:
        con.execute("INSERT INTO api_keys (id, caller, tenant_id, key_prefix, key_hash, created_at) "
                    "VALUES ('k2', 'caller-a', 'default', 'bbbbbbbbbbbb', 'hmac256$00', '2026-01-01 00:00:00')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("pre Day 3 schema should refuse a second row per caller")
    con.close()

    alembic("upgrade", "head")
    con = sqlite3.connect(db_path)
    columns = [c[1] for c in con.execute("PRAGMA table_info(api_keys)")]
    assert "superseded_by" in columns and "previous_valid_until" in columns
    indexes = {r[1]: r[2] for r in con.execute("PRAGMA index_list(api_keys)")}
    assert indexes.get("ux_api_keys_caller_active") == 1
    con.execute("UPDATE api_keys SET superseded_by = 'k2', previous_valid_until = '2026-01-02 00:00:00' WHERE id = 'k1'")
    con.execute("INSERT INTO api_keys (id, caller, tenant_id, key_prefix, key_hash, created_at) "
                "VALUES ('k2', 'caller-a', 'default', 'bbbbbbbbbbbb', 'hmac256$00', '2026-01-02 00:00:00')")
    con.commit()
    try:
        con.execute("INSERT INTO api_keys (id, caller, tenant_id, key_prefix, key_hash, created_at) "
                    "VALUES ('k3', 'caller-a', 'default', 'cccccccccccc', 'hmac256$00', '2026-01-03 00:00:00')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("two active keys for one caller must be refused")
    con.close()

    alembic("downgrade", "d1e2f3a4b5c6")
    con = sqlite3.connect(db_path)
    assert [r[0] for r in con.execute("SELECT id FROM api_keys ORDER BY id")] == ["k2"]
    columns = [c[1] for c in con.execute("PRAGMA table_info(api_keys)")]
    assert "superseded_by" not in columns
    try:
        con.execute("INSERT INTO api_keys (id, caller, tenant_id, key_prefix, key_hash, created_at, scopes) "
                    "VALUES ('k4', 'caller-a', 'default', 'dddddddddddd', 'hmac256$00', '2026-01-04 00:00:00', '[]')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("downgrade must restore the plain unique constraint on caller")
    con.close()
    assert list(SCOPES)
