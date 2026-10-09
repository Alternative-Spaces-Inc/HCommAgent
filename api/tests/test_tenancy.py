"""HCA-P19 Day 1: tenant isolation, server side stream authorization,
control match on answer, expires_at always set.

Two tenants (alpha, beta), two devices each, one api key each. Nothing of
alpha's (title, options, answer, audit row, stream envelope) reaches a beta
device or beta key, and the refusals are 404s indistinguishable from a
missing request."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_tenancy.db")

import json
import pytest
import secrets
import time
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine, SessionLocal
from app import models
from app.apikeys import issue_api_key
from app.devicesig import canonical_string, device_id_for_public_key_pem
from app.inbox_api import inbox_event_source
from authhelper import ADMIN_AUTH
from deviceauth import raw_signature

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)


class Dev:
    def __init__(self, tenant_id: str, label: str):
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.pem = self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.id = device_id_for_public_key_pem(self.pem)
        self.tenant_id = tenant_id
        r = client.post("/v1/devices", json={"label": label, "public_key_pem": self.pem, "tenant_id": tenant_id}, headers=ADMIN_AUTH)
        assert r.status_code == 201, r.text
        assert r.json()["tenant_id"] == tenant_id

    def headers(self, method, path, body=b""):
        ts = str(int(time.time()))
        nonce = secrets.token_urlsafe(12)
        return {"X-HCA-Device": self.id, "X-HCA-Timestamp": ts, "X-HCA-Nonce": nonce,
                "X-HCA-Signature": raw_signature(self.private, canonical_string(ts, nonce, method, path, body))}

    def get(self, path):
        return client.get(path, headers=self.headers("GET", path))

    def post(self, path, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        h = self.headers("POST", path, body)
        h["Content-Type"] = "application/json"
        return client.post(path, content=body, headers=h)

    def row(self):
        with SessionLocal() as db:
            d = db.get(models.Device, self.id)
            db.expunge(d)
            return d


def _tenant(slug):
    with SessionLocal() as db:
        models.ensure_default_tenant(db)
    r = client.post("/v1/tenants", json={"slug": slug, "name": slug.title()}, headers=ADMIN_AUTH)
    assert r.status_code == 201, r.text
    return r.json()["tenant_id"]


def _key(tenant_id, caller):
    with SessionLocal() as db:
        return {"Authorization": "Bearer " + issue_api_key(db, caller, tenant_id)["api_key"]}


def _ask_body(caller, title, control="approve_deny"):
    return {
        "caller": caller, "audience": "owner", "title": title, "control": control,
        "options_or_fields": [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}],
        "suggested": {"option_id": "approve", "why": "test"},
        "deliver_to": {"mode": "poll"},
    }


class S:
    """Two tenants, a key and two devices each. Built by the module fixture
    below rather than at import: every test module rebuilds the shared
    SQLite schema at import time, so import-time rows would be gone by the
    time these tests run."""
    ALPHA = BETA = KEY_A = KEY_B = A1 = A2 = B1 = B2 = None


@pytest.fixture(scope="module", autouse=True)
def _env():
    S.ALPHA = _tenant("alpha")
    S.BETA = _tenant("beta")
    S.KEY_A = _key(S.ALPHA, "alpha-agent")
    S.KEY_B = _key(S.BETA, "beta-agent")
    S.A1, S.A2 = Dev(S.ALPHA, "alpha one"), Dev(S.ALPHA, "alpha two")
    S.B1, S.B2 = Dev(S.BETA, "beta one"), Dev(S.BETA, "beta two")
    for d in (S.A1, S.A2, S.B1, S.B2):
        assert d.get("/v1/inbox").status_code == 200  # marks last_seen so routing counts them


def _create(key, caller, title):
    r = client.post("/v1/requests", json=_ask_body(caller, title), headers=key)
    assert r.status_code == 201, r.text
    return r.json()["request_id"]


def test_inbox_and_stream_never_cross_tenants():
    rid = _create(S.KEY_A, "alpha-agent", "S.ALPHA SECRET TITLE")

    assert [i["request_id"] for i in S.A1.get("/v1/inbox").json()] == [rid]
    assert [i["request_id"] for i in S.A2.get("/v1/inbox").json()] == [rid]
    for d in (S.B1, S.B2):
        body = d.get("/v1/inbox").text
        assert "S.ALPHA SECRET" not in body and rid not in body

    async def run():
        ga = inbox_event_source(S.A1.row(), keepalive_seconds=5)
        gb = inbox_event_source(S.B1.row(), keepalive_seconds=5)
        await ga.__anext__()
        await gb.__anext__()
        rid2 = _create(S.KEY_A, "alpha-agent", "S.ALPHA STREAMED TITLE")
        chunk = await asyncio.wait_for(ga.__anext__(), timeout=2)
        assert "S.ALPHA STREAMED TITLE" in chunk and rid2 in chunk
        try:
            leaked = await asyncio.wait_for(gb.__anext__(), timeout=0.3)
            assert False, f"beta stream received alpha data: {leaked[:80]}"
        except asyncio.TimeoutError:
            pass
        await ga.aclose()
        await gb.aclose()

    asyncio.run(run())


def test_other_tenant_cannot_read_answer_cancel_or_audit():
    rid = _create(S.KEY_A, "alpha-agent", "alpha only")

    # beta key: the ask does not exist as far as it can tell
    assert client.get(f"/v1/requests/{rid}", headers=S.KEY_B).status_code == 404
    assert client.post(f"/v1/requests/{rid}/cancel", json={"reason": "x"}, headers=S.KEY_B).status_code == 404

    # beta device: same 404, and nothing changed
    r = S.B1.post(f"/v1/requests/{rid}/answer", {"control": "approve_deny", "value": "deny", "surface": "browser_overlay"})
    assert r.status_code == 404
    with SessionLocal() as db:
        assert db.get(models.Ask, rid).state == "queued"

    # alpha device answers; beta audit stays empty, alpha audit shows it
    r = S.A1.post(f"/v1/requests/{rid}/answer", {"control": "approve_deny", "value": "approve", "surface": "browser_overlay"})
    assert r.status_code == 201, r.text
    assert rid in {row["request_id"] for row in S.A2.get("/v1/audit").json()}
    beta_audit = S.B2.get("/v1/audit").text
    assert rid not in beta_audit and "alpha only" not in beta_audit


def test_answer_control_must_match_the_request_control():
    rid = _create(S.KEY_A, "alpha-agent", "control check")
    r = S.A1.post(f"/v1/requests/{rid}/answer", {"control": "radio", "value": "approve", "surface": "browser_overlay"})
    assert r.status_code == 422
    assert "does not match" in r.json()["detail"]
    with SessionLocal() as db:
        assert db.get(models.Ask, rid).state == "queued"
    r = S.A1.post(f"/v1/requests/{rid}/answer", {"control": "approve_deny", "value": "approve", "surface": "browser_overlay"})
    assert r.status_code == 201


def test_expires_at_is_always_set_and_deadline_is_validated():
    rid = _create(S.KEY_A, "alpha-agent", "no deadline")
    view = client.get(f"/v1/requests/{rid}", headers=S.KEY_A).json()
    assert view["expires_at"] is not None
    created = datetime.fromisoformat(view["created_at"]).replace(tzinfo=timezone.utc)
    expires = datetime.fromisoformat(view["expires_at"]).replace(tzinfo=timezone.utc)
    assert timedelta(days=6, hours=23) < expires - created <= timedelta(days=7)

    past = _ask_body("alpha-agent", "past") | {"deadline_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}
    assert client.post("/v1/requests", json=past, headers=S.KEY_A).status_code == 422
    far = _ask_body("alpha-agent", "far") | {"deadline_at": (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()}
    assert client.post("/v1/requests", json=far, headers=S.KEY_A).status_code == 422
    soon = _ask_body("alpha-agent", "soon") | {"deadline_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    r = client.post("/v1/requests", json=soon, headers=S.KEY_A)
    assert r.status_code == 201
    view = client.get(f"/v1/requests/{r.json()['request_id']}", headers=S.KEY_A).json()
    assert view["expires_at"].startswith(view["request"]["deadline_at"][:16])


def test_revoked_device_loses_an_open_stream():
    victim = Dev(S.ALPHA, "alpha short lived")
    assert victim.get("/v1/inbox").status_code == 200

    async def run():
        gen = inbox_event_source(victim.row(), keepalive_seconds=0.05)
        await gen.__anext__()
        assert client.post(f"/v1/devices/{victim.id}/revoke", headers=ADMIN_AUTH).status_code == 200
        chunks = []
        try:
            while True:
                chunks.append(await asyncio.wait_for(gen.__anext__(), timeout=1))
        except StopAsyncIteration:
            pass
        assert chunks[-1].startswith("event: stream.closed")
        # and a fresh signed call is refused outright
        assert victim.get("/v1/inbox").status_code == 403

    asyncio.run(run())


def test_tenant_admin_routes_and_key_tenant_binding():
    r = client.get("/v1/tenants", headers=ADMIN_AUTH)
    assert {t["slug"] for t in r.json()} >= {"default", "alpha", "beta"}
    assert client.post("/v1/tenants", json={"slug": "alpha", "name": "dup"}, headers=ADMIN_AUTH).status_code == 409
    assert client.post("/v1/tenants", json={"slug": "Bad Slug", "name": "x"}, headers=ADMIN_AUTH).status_code == 422
    assert client.get("/v1/tenants", headers={}).status_code == 401

    # owners allowlist per tenant
    r = client.post(f"/v1/tenants/{S.BETA}/owners", json={"email": "Lead@Example.com"}, headers=ADMIN_AUTH)
    assert r.status_code == 201 and r.json()["owner_emails"] == ["lead@example.com"]
    assert client.get(f"/v1/tenants/{S.ALPHA}", headers=ADMIN_AUTH).json()["owner_emails"] == []
    r = client.delete(f"/v1/tenants/{S.BETA}/owners/lead@example.com", headers=ADMIN_AUTH)
    assert r.status_code == 200 and r.json()["owner_emails"] == []

    # a key is minted inside a tenant and a caller cannot be moved across
    r = client.post("/v1/api-keys", json={"caller": "beta-agent", "tenant_id": S.ALPHA}, headers=ADMIN_AUTH)
    assert r.status_code == 422
    r = client.post("/v1/api-keys", json={"caller": "gamma-agent", "tenant_id": "no-such-tenant"}, headers=ADMIN_AUTH)
    assert r.status_code == 422
    r = client.post("/v1/api-keys", json={"caller": "default-agent"}, headers=ADMIN_AUTH)
    assert r.status_code == 201 and r.json()["tenant_id"] == models.DEFAULT_TENANT_ID

    # a device key registered under one tenant cannot be re-registered under another
    r = client.post("/v1/devices", json={"label": "moved", "public_key_pem": S.A1.pem, "tenant_id": S.BETA}, headers=ADMIN_AUTH)
    assert r.status_code == 409


def test_disabling_a_tenant_revokes_its_keys_and_devices():
    gamma = _tenant("gamma")
    key = _key(gamma, "gamma-agent")
    dev = Dev(gamma, "gamma device")
    assert dev.get("/v1/inbox").status_code == 200
    assert client.post("/v1/requests", json=_ask_body("gamma-agent", "before"), headers=key).status_code == 201

    r = client.post(f"/v1/tenants/{gamma}/disable", headers=ADMIN_AUTH)
    assert r.status_code == 200 and r.json()["disabled_at"]
    assert client.post("/v1/requests", json=_ask_body("gamma-agent", "after"), headers=key).status_code == 403
    assert dev.get("/v1/inbox").status_code == 403
    assert client.post(f"/v1/tenants/{models.DEFAULT_TENANT_ID}/disable", headers=ADMIN_AUTH).status_code == 409


def test_google_link_uses_the_tenant_allowlist(monkeypatch):
    from app import googleauth
    monkeypatch.setenv("HCA_GOOGLE_CLIENT_IDS", "client-1")
    monkeypatch.setenv("HCA_OWNER_EMAILS", "owner@default.example")
    googleauth.set_fetcher(lambda token: (200, {"aud": "client-1", "email": "lead@beta.example", "email_verified": True, "expires_in": 100, "sub": "sub-1"}))
    try:
        # beta has no owner rows: nobody may link (no fallback for non default tenants)
        assert S.B1.post("/v1/devices/self/google-link", {"access_token": "t"}).status_code == 403
        client.post(f"/v1/tenants/{S.BETA}/owners", json={"email": "lead@beta.example"}, headers=ADMIN_AUTH)
        r = S.B1.post("/v1/devices/self/google-link", {"access_token": "t"})
        assert r.status_code == 200 and r.json()["google_email"] == "lead@beta.example"
        # alpha's allowlist does not contain that account
        assert S.A1.post("/v1/devices/self/google-link", {"access_token": "t"}).status_code == 403
    finally:
        googleauth.set_fetcher(None)
