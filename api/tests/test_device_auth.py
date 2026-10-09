"""HCA-P6: per-device ECDSA request signing on the human surface routes
(app/devicesig.py, app/auth.py require_device, app/devices_api.py)."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("DATABASE_URL", "sqlite:///./hca_test_device_auth.db")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from app.db import Base, engine
from app import devicesig
from authhelper import agent_auth, ADMIN_AUTH
from deviceauth import DEVICE_ID, DEVICE_LABEL, PUBLIC_PEM, human_get, human_headers, ensure_registered

Base.metadata.create_all(bind=engine)
client = TestClient(app)


def _ask(caller="agent-dev-1"):
    return {
        "caller": caller, "audience": "owner", "title": "device auth ask", "control": "radio",
        "options_or_fields": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "suggested": {"option_id": "a", "why": "A"}, "deliver_to": {"mode": "webhook", "url": "https://example.test/hook"},
    }


def _new_key():
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return k, pem


def test_device_id_is_derived_from_the_key_and_stable():
    a = devicesig.device_id_for_public_key_pem(PUBLIC_PEM)
    assert a == DEVICE_ID and a.startswith("dev_") and len(a) == 36
    _, other = _new_key()
    assert devicesig.device_id_for_public_key_pem(other) != a


def test_register_list_revoke_need_admin_bearer_and_are_idempotent():
    _, pem = _new_key()
    assert client.post("/v1/devices", json={"label": "x", "public_key_pem": pem}).status_code == 401
    assert client.get("/v1/devices").status_code == 401
    r1 = client.post("/v1/devices", json={"label": "laptop", "public_key_pem": pem}, headers=ADMIN_AUTH)
    assert r1.status_code == 201, r1.text
    did = r1.json()["device_id"]
    assert did == devicesig.device_id_for_public_key_pem(pem)
    r2 = client.post("/v1/devices", json={"label": "laptop renamed", "public_key_pem": pem}, headers=ADMIN_AUTH)
    assert r2.status_code == 201 and r2.json()["device_id"] == did and r2.json()["label"] == "laptop renamed"
    listed = client.get("/v1/devices", headers=ADMIN_AUTH).json()
    assert any(d["device_id"] == did for d in listed)
    rv = client.post(f"/v1/devices/{did}/revoke", headers=ADMIN_AUTH)
    assert rv.status_code == 200 and rv.json()["revoked_at"] is not None
    again = client.post("/v1/devices", json={"label": "laptop", "public_key_pem": pem}, headers=ADMIN_AUTH)
    assert again.status_code == 409
    assert client.post("/v1/devices/dev_nope/revoke", headers=ADMIN_AUTH).status_code == 404


def test_register_rejects_a_non_p256_key():
    from cryptography.hazmat.primitives.asymmetric import rsa
    rsa_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    assert client.post("/v1/devices", json={"label": "rsa", "public_key_pem": rsa_pem}, headers=ADMIN_AUTH).status_code == 422
    assert client.post("/v1/devices", json={"label": "junk", "public_key_pem": "not a pem"}, headers=ADMIN_AUTH).status_code == 422


def test_unknown_and_revoked_devices_are_403():
    k, pem = _new_key()
    did = devicesig.device_id_for_public_key_pem(pem)
    h = human_headers("GET", "/v1/inbox", private_key=k, device_id=did)
    assert client.get("/v1/inbox", headers=h).status_code == 403
    client.post("/v1/devices", json={"label": "short lived", "public_key_pem": pem}, headers=ADMIN_AUTH)
    h = human_headers("GET", "/v1/inbox", private_key=k, device_id=did)
    assert client.get("/v1/inbox", headers=h).status_code == 200
    client.post(f"/v1/devices/{did}/revoke", headers=ADMIN_AUTH)
    h = human_headers("GET", "/v1/inbox", private_key=k, device_id=did)
    r = client.get("/v1/inbox", headers=h)
    assert r.status_code == 403 and "revoked" in r.json()["detail"]


def test_bad_signature_wrong_key_and_tampered_path_are_401():
    ensure_registered()
    k, _ = _new_key()
    # signed by a key that is not the registered device's key
    h = human_headers("GET", "/v1/inbox", private_key=k)
    assert client.get("/v1/inbox", headers=h).status_code == 401
    # signed for one path, sent to another
    h = human_headers("GET", "/v1/inbox")
    assert client.get("/v1/audit", headers=h).status_code == 401
    # signed for one query string, sent with another
    h = human_headers("GET", "/v1/audit?agent=one")
    assert client.get("/v1/audit?agent=two", headers=h).status_code == 401
    assert client.get("/v1/audit?agent=one", headers=h).status_code == 200
    # garbage signature
    h = human_headers("GET", "/v1/inbox")
    h["X-HCA-Signature"] = "AAAA"
    assert client.get("/v1/inbox", headers=h).status_code == 401
    # missing one header
    h = human_headers("GET", "/v1/inbox")
    del h["X-HCA-Nonce"]
    assert client.get("/v1/inbox", headers=h).status_code == 401


def test_stale_timestamp_is_401():
    old = int(time.time()) - devicesig.MAX_SKEW_SECONDS - 5
    h = human_headers("GET", "/v1/inbox", timestamp=old)
    r = client.get("/v1/inbox", headers=h)
    assert r.status_code == 401 and "stale" in r.json()["detail"]
    future = int(time.time()) + devicesig.MAX_SKEW_SECONDS + 5
    assert client.get("/v1/inbox", headers=human_headers("GET", "/v1/inbox", timestamp=future)).status_code == 401
    h = human_headers("GET", "/v1/inbox")
    h["X-HCA-Timestamp"] = "yesterday"
    assert client.get("/v1/inbox", headers=h).status_code == 401


def test_replayed_request_is_401():
    h = human_headers("GET", "/v1/inbox")
    assert client.get("/v1/inbox", headers=h).status_code == 200
    r = client.get("/v1/inbox", headers=h)
    assert r.status_code == 401 and "replayed" in r.json()["detail"]


def test_tampered_body_is_401_and_answer_records_the_device():
    rid = client.post("/v1/requests", json=_ask(), headers=agent_auth("agent-dev-1")).json()["request_id"]
    import json as _json
    body = _json.dumps({"control": "radio", "value": "a", "surface": "browser_overlay", "device": "spoofed"}, separators=(",", ":")).encode()
    h = human_headers("POST", f"/v1/requests/{rid}/answer", body)
    h["Content-Type"] = "application/json"
    tampered = body.replace(b'"value":"a"', b'"value":"b"')
    assert client.post(f"/v1/requests/{rid}/answer", content=tampered, headers=h).status_code == 401
    ok = client.post(f"/v1/requests/{rid}/answer", content=body, headers=h)
    assert ok.status_code == 201, ok.text
    rec = ok.json()
    # the server records the verified device, not what the body claimed
    assert rec["device"] == DEVICE_ID
    assert rec["answered_by"] == DEVICE_LABEL
    audit = human_get(client, "/v1/audit", params={"agent": "agent-dev-1"}).json()
    assert any(a["request_id"] == rid and a["device"] == DEVICE_ID for a in audit)


def test_openapi_declares_the_device_scheme_on_the_human_routes():
    spec = client.get("/openapi.json").json()
    for path, method in [("/v1/inbox", "get"), ("/v1/inbox/stream", "get"), ("/v1/audit", "get"), ("/v1/requests/{request_id}/answer", "post")]:
        sec = spec["paths"][path][method].get("security", [])
        assert any("DeviceSignature" in s for s in sec), (path, sec)
    assert "DeviceSignature" in spec["components"]["securitySchemes"]
