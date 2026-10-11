"""HCA-P21a Day 1: the webhook destination registry routes and decision 7's
400 on an unregistered deliver_to.url."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_webhook_destinations.db")

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import Base, engine, SessionLocal
from app import models, ssrf, webhook_destinations as registry
from app.apikeys import _hash_secret
from authhelper import agent_auth

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)
PUBLIC = "93.184.216.34"


def _ask(caller, url):
    return {"caller": caller, "audience": "owner", "title": "t", "why": "w", "control": "radio",
            "options_or_fields": [{"id": "a", "label": "A"}], "suggested": {"option_id": "a", "why": "a"},
            "deliver_to": {"mode": "webhook", "url": url}}


def _events(destination_id):
    with SessionLocal() as db:
        return [(e.action, e.actor) for e in db.execute(select(models.WebhookDestinationEvent).where(
            models.WebhookDestinationEvent.destination_id == destination_id).order_by(models.WebhookDestinationEvent.at)).scalars()]


def test_register_returns_the_secret_once_and_pins_addresses():
    r = client.post("/v1/webhooks", json={"url": "https://hooks.example/a"}, headers=agent_auth("reg-a"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("whd_")
    assert body["secret"].startswith("whsec_") and len(body["secret"]) > 40
    assert body["secret_prefix"] == body["secret"][:14]
    assert body["pinned_addresses"] == [PUBLIC]
    assert body["caller"] == "reg-a" and body["revoked_at"] is None
    listed = client.get("/v1/webhooks", headers=agent_auth("reg-a")).json()
    ours = [d for d in listed if d["id"] == body["id"]]
    assert len(ours) == 1 and "secret" not in ours[0]
    with SessionLocal() as db:
        row = db.get(models.WebhookDestination, body["id"])
        assert row.secret_hash == _hash_secret(body["secret"])
        assert row.secret_hash.startswith("hmac256$")
    assert _events(body["id"]) == [("registered", "reg-a")]


def test_register_rejects_forbidden_urls_with_400():
    for url in ["http://hooks.example/a", "https://10.0.0.1/a", "https://metadata.google.internal/x", "https://[::ffff:127.0.0.1]/x"]:
        r = client.post("/v1/webhooks", json={"url": url}, headers=agent_auth("reg-b"))
        assert r.status_code == 400, (url, r.text)
        assert r.json()["detail"].startswith("url rejected:")


def test_register_rejects_a_host_resolving_to_a_private_address():
    h = agent_auth("reg-c")  # minted before the resolver turns hostile
    ssrf.set_resolver(lambda host: ["10.20.30.40"])
    try:
        r = client.post("/v1/webhooks", json={"url": "https://inside.example/a"}, headers=h)
    finally:
        ssrf.set_resolver(lambda host: [PUBLIC])
    assert r.status_code == 400 and "private address" in r.json()["detail"]


def test_same_url_twice_is_400_until_revoked():
    h = agent_auth("reg-d")
    first = client.post("/v1/webhooks", json={"url": "https://hooks.example/d"}, headers=h)
    assert first.status_code == 201
    again = client.post("/v1/webhooks", json={"url": "https://hooks.example/d"}, headers=h)
    assert again.status_code == 400 and "already registered" in again.json()["detail"]
    assert client.delete(f"/v1/webhooks/{first.json()['id']}", headers=h).status_code == 200
    third = client.post("/v1/webhooks", json={"url": "https://hooks.example/d"}, headers=h)
    assert third.status_code == 201 and third.json()["id"] != first.json()["id"]


def test_rotate_returns_a_new_secret_and_keeps_the_old_one_24h():
    h = agent_auth("reg-e")
    reg = client.post("/v1/webhooks", json={"url": "https://hooks.example/e"}, headers=h).json()
    rot = client.post(f"/v1/webhooks/{reg['id']}/rotate", headers=h)
    assert rot.status_code == 200, rot.text
    body = rot.json()
    assert body["secret"] != reg["secret"] and body["secret_prefix"] != reg["secret_prefix"]
    assert body["rotated_at"] is not None and body["previous_valid_until"] is not None
    with SessionLocal() as db:
        row = db.get(models.WebhookDestination, reg["id"])
        now = datetime.now(timezone.utc)
        assert registry.current_secret_hashes(row, now) == [_hash_secret(body["secret"]), _hash_secret(reg["secret"])]
        assert registry.current_secret_hashes(row, now + timedelta(hours=25)) == [_hash_secret(body["secret"])]
    assert _events(reg["id"]) == [("registered", "reg-e"), ("rotated", "reg-e")]


def test_revoke_is_idempotent_and_audited_and_listing_hides_revoked_by_default():
    h = agent_auth("reg-f")
    reg = client.post("/v1/webhooks", json={"url": "https://hooks.example/f"}, headers=h).json()
    r1 = client.delete(f"/v1/webhooks/{reg['id']}", headers=h)
    assert r1.status_code == 200 and r1.json()["revoked_at"] is not None
    r2 = client.delete(f"/v1/webhooks/{reg['id']}", headers=h)
    assert r2.status_code == 200 and r2.json()["revoked_at"] == r1.json()["revoked_at"]
    assert _events(reg["id"]) == [("registered", "reg-f"), ("revoked", "reg-f")]
    active_ids = [d["id"] for d in client.get("/v1/webhooks", headers=h).json()]
    assert reg["id"] not in active_ids
    all_ids = [d["id"] for d in client.get("/v1/webhooks", params={"include_revoked": "true"}, headers=h).json()]
    assert reg["id"] in all_ids
    rot = client.post(f"/v1/webhooks/{reg['id']}/rotate", headers=h)
    assert rot.status_code == 400 and "revoked" in rot.json()["detail"]


def test_another_callers_destination_is_404():
    reg = client.post("/v1/webhooks", json={"url": "https://hooks.example/g"}, headers=agent_auth("reg-g")).json()
    other = agent_auth("reg-g-other")
    assert client.post(f"/v1/webhooks/{reg['id']}/rotate", headers=other).status_code == 404
    assert client.delete(f"/v1/webhooks/{reg['id']}", headers=other).status_code == 404
    assert reg["id"] not in [d["id"] for d in client.get("/v1/webhooks", headers=other).json()]


def test_routes_need_an_agent_key():
    assert client.post("/v1/webhooks", json={"url": "https://hooks.example/h"}).status_code == 401
    assert client.get("/v1/webhooks").status_code == 401


def test_ask_with_unregistered_url_is_400_and_registered_url_stores_destination_id():
    h = agent_auth("reg-i")
    bad = client.post("/v1/requests", json=_ask("reg-i", "https://hooks.example/not-registered"), headers=h)
    assert bad.status_code == 400
    assert "not a registered webhook destination" in bad.json()["detail"]
    reg = client.post("/v1/webhooks", json={"url": "https://hooks.example/i"}, headers=h).json()
    good = client.post("/v1/requests", json=_ask("reg-i", "https://hooks.example/i"), headers=h)
    assert good.status_code == 201, good.text
    with SessionLocal() as db:
        ask = db.get(models.Ask, good.json()["request_id"])
        assert ask.deliver_to["destination_id"] == reg["id"]
    # another caller's registration of the same url does not count
    other = agent_auth("reg-i-other")
    assert client.post("/v1/requests", json=_ask("reg-i-other", "https://hooks.example/i"), headers=other).status_code == 400
    # a revoked destination no longer qualifies
    client.delete(f"/v1/webhooks/{reg['id']}", headers=h)
    assert client.post("/v1/requests", json=_ask("reg-i", "https://hooks.example/i"), headers=h).status_code == 400


def test_poll_mode_asks_need_no_destination():
    body = _ask("reg-j", None)
    body["deliver_to"] = {"mode": "poll"}
    r = client.post("/v1/requests", json=body, headers=agent_auth("reg-j"))
    assert r.status_code == 201 and r.json()["state"] == "queued"


def test_caller_supplied_destination_id_is_ignored():
    h = agent_auth("reg-k")
    reg = client.post("/v1/webhooks", json={"url": "https://hooks.example/k"}, headers=h).json()
    body = _ask("reg-k", "https://hooks.example/k")
    body["deliver_to"]["destination_id"] = "whd_forged"
    r = client.post("/v1/requests", json=body, headers=h)
    assert r.status_code == 201
    with SessionLocal() as db:
        assert db.get(models.Ask, r.json()["request_id"]).deliver_to["destination_id"] == reg["id"]
