"""Surface routing (HCA-P14, app/routing.py).

Pure rule tests plus the inbox filter end to end with two devices: the
process test device (deviceauth.py) and a second key registered here at
a later ladder position.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_routing.db")
os.environ["HCA_SURFACE_FALLBACK_SECONDS"] = "90"

from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine, SessionLocal
from app import models, routing
from app.devicesig import device_id_for_public_key_pem
from authhelper import agent_auth, ADMIN_AUTH
import deviceauth
from deviceauth import human_get, human_headers, ensure_registered, DEVICE_ID

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)

_second_private = ec.generate_private_key(ec.SECP256R1())
SECOND_PEM = _second_private.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
SECOND_ID = device_id_for_public_key_pem(SECOND_PEM)


def _dev(pri, seen_ago=0, id_="x", google="sub"):
    return models.Device(id=id_, label=id_, surface="browser_overlay", priority=pri, public_key_pem="k",
                         created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                         last_seen_at=datetime.now(timezone.utc) - timedelta(seconds=seen_ago), google_sub=google)


def test_rank_equal_priorities_share_rank_and_lower_is_earlier():
    a, b, c = _dev(10, id_="a"), _dev(100, id_="b"), _dev(100, id_="c")
    elig = [a, b, c]
    assert routing.rank_of(a, elig) == 0
    assert routing.rank_of(b, elig) == 1
    assert routing.rank_of(c, elig) == 1
    assert routing.rank_count(elig) == 2


def test_window_half_for_red_gate_and_visible_from():
    assert routing.window_seconds(False, 90) == 90
    assert routing.window_seconds(True, 90) == 45
    t0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    assert routing.visible_from(t0, 0, False) == t0
    assert routing.visible_from(t0, 2, False) == t0 + timedelta(seconds=180)
    assert routing.visible_from(t0, 1, True) == t0 + timedelta(seconds=45)
    # naive stored timestamps are read as UTC
    assert routing.visible_from(t0.replace(tzinfo=None), 1, False) == t0 + timedelta(seconds=90)


def test_escalation_delays_one_per_later_rank():
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    assert routing.escalation_delays(now, 1, False, now) == []
    assert routing.escalation_delays(now, 3, False, now) == [90.0, 180.0]
    assert routing.escalation_delays(now - timedelta(seconds=100), 2, False, now) == [0.0]


def _quiet_others():
    """Other test modules register devices of their own into the shared DB;
    park them outside the active window so they do not take ranks here."""
    with SessionLocal() as db:
        for d in db.query(models.Device).all():
            if d.id not in (DEVICE_ID, SECOND_ID):
                d.last_seen_at = None
        db.commit()


def _register_second(priority):
    _quiet_others()
    r = client.post("/v1/devices", json={"label": "second", "public_key_pem": SECOND_PEM}, headers=ADMIN_AUTH)
    assert r.status_code in (200, 201)
    r = client.patch(f"/v1/devices/{SECOND_ID}", json={"priority": priority}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["priority"] == priority
    return r.json()


def _second_get(path):
    h = human_headers("GET", path, private_key=_second_private, device_id=SECOND_ID)
    return client.get(path, headers=h)


def _post_ask(title, red_gate=False):
    body = {"caller": "test-agent", "audience": "owner", "title": title, "control": "radio",
            "options_or_fields": [{"id": "a", "label": "A"}], "suggested": {"option_id": "a", "why": "w"},
            "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"}}
    if red_gate:
        body["red_gate"] = {"resource": "r", "change": "c", "impact": "i"}
    r = client.post("/v1/requests", json=body, headers=agent_auth("test-agent"))
    assert r.status_code == 201, r.text
    return r.json()["request_id"]


def test_patch_priority_validates_and_defaults_to_100():
    ensure_registered()
    r = client.get("/v1/devices", headers=ADMIN_AUTH)
    me = [d for d in r.json() if d["device_id"] == DEVICE_ID][0]
    assert me["priority"] == 100
    r = client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 5000}, headers=ADMIN_AUTH)
    assert r.status_code == 422
    r = client.patch("/v1/devices/dev_missing", json={"priority": 1}, headers=ADMIN_AUTH)
    assert r.status_code == 404
    r = client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 1}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code in (401, 403)


def test_inbox_hides_fresh_ask_from_later_rank_until_window_passes():
    # primary = process device at priority 10, secondary at 200; both seen now
    ensure_registered()
    client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 10}, headers=ADMIN_AUTH)
    _register_second(200)
    assert _second_get("/v1/inbox").status_code == 200  # marks it seen
    rid = _post_ask("routed ask")
    assert rid in [i["request_id"] for i in human_get(client, "/v1/inbox").json()]
    assert rid not in [i["request_id"] for i in _second_get("/v1/inbox").json()]
    # age the ask past one window: the secondary now sees it
    with SessionLocal() as db:
        ask = db.get(models.Ask, rid)
        ask.created_at = datetime.now(timezone.utc) - timedelta(seconds=91)
        db.commit()
    assert rid in [i["request_id"] for i in _second_get("/v1/inbox").json()]
    # red gate: half window
    rid2 = _post_ask("red routed", red_gate=True)
    assert rid2 not in [i["request_id"] for i in _second_get("/v1/inbox").json()]
    with SessionLocal() as db:
        ask = db.get(models.Ask, rid2)
        ask.created_at = datetime.now(timezone.utc) - timedelta(seconds=46)
        db.commit()
    assert rid2 in [i["request_id"] for i in _second_get("/v1/inbox").json()]


def test_inactive_primary_drops_out_of_ranking():
    ensure_registered()
    client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 10}, headers=ADMIN_AUTH)
    _register_second(200)
    with SessionLocal() as db:
        me = db.get(models.Device, DEVICE_ID)
        me.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=routing.active_seconds() + 5)
        db.commit()
    rid = _post_ask("primary asleep")
    # the secondary is the only active device, so it is rank 0 and sees it at once
    assert rid in [i["request_id"] for i in _second_get("/v1/inbox").json()]


def test_created_event_names_rank0_and_fire_escalation_names_later_rank(monkeypatch):
    ensure_registered()
    client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 10}, headers=ADMIN_AUTH)
    _register_second(200)
    human_get(client, "/v1/inbox"); _second_get("/v1/inbox")
    from app import requests_api
    seen = []
    monkeypatch.setattr(requests_api, "publish", lambda e, d: seen.append((e, d)) or {"event": e, "data": d})
    rid = _post_ask("event ask")
    created = [d for e, d in seen if e == "request.created" and d["request_id"] == rid][0]
    assert created["visible_to"] == [DEVICE_ID]
    # before the window: nothing to escalate to
    assert routing.fire_escalation(rid, SessionLocal, lambda: False, lambda e, d: (e, d)) is None
    with SessionLocal() as db:
        ask = db.get(models.Ask, rid)
        ask.created_at = datetime.now(timezone.utc) - timedelta(seconds=91)
        db.commit()
    e, d = routing.fire_escalation(rid, SessionLocal, lambda: False, lambda e, d: (e, d))
    assert e == "request.escalated"
    assert d["visible_to"] == [DEVICE_ID, SECOND_ID]
    assert d["request_id"] == rid and d["title"] == "event ask"
    # answered ask: no escalation
    with SessionLocal() as db:
        ask = db.get(models.Ask, rid); ask.state = "answered"; db.commit()
    assert routing.fire_escalation(rid, SessionLocal, lambda: False, lambda e, d: (e, d)) is None


def test_default_priorities_keep_pre_p14_behaviour():
    ensure_registered()
    client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 100}, headers=ADMIN_AUTH)
    _register_second(100)
    human_get(client, "/v1/inbox"); _second_get("/v1/inbox")
    rid = _post_ask("everyone at once")
    assert rid in [i["request_id"] for i in human_get(client, "/v1/inbox").json()]
    assert rid in [i["request_id"] for i in _second_get("/v1/inbox").json()]


def test_device_sets_its_own_priority_with_a_signed_patch():
    ensure_registered()
    client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 100}, headers=ADMIN_AUTH)
    body = b'{"priority":10}'
    h = human_headers("PATCH", "/v1/devices/self", body)
    h["Content-Type"] = "application/json"
    r = client.patch("/v1/devices/self", content=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["priority"] == 10
    assert r.json()["device_id"] == DEVICE_ID
    me = human_get(client, "/v1/devices/self").json()
    assert me["priority"] == 10
    # out of range refused, unsigned refused, admin view agrees
    body = b'{"priority":5000}'
    h = human_headers("PATCH", "/v1/devices/self", body)
    h["Content-Type"] = "application/json"
    assert client.patch("/v1/devices/self", content=body, headers=h).status_code == 422
    assert client.patch("/v1/devices/self", json={"priority": 1}).status_code in (401, 403)
    admin = [d for d in client.get("/v1/devices", headers=ADMIN_AUTH).json() if d["device_id"] == DEVICE_ID][0]
    assert admin["priority"] == 10
    client.patch(f"/v1/devices/{DEVICE_ID}", json={"priority": 100}, headers=ADMIN_AUTH)
