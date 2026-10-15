"""P23 Day 3 signed device key rotation."""
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from app.main import app
from app.devicesig import device_id_for_public_key_pem
from authhelper import ADMIN_AUTH
from deviceauth import human_headers

client = TestClient(app)

def keypair():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return key, pem

def test_signed_rotation_carries_device_and_overlap_and_is_idempotent():
    old_key, old_pem = keypair()
    new_key, new_pem = keypair()
    old_id = device_id_for_public_key_pem(old_pem)
    registered = client.post('/v1/devices', json={'label':'rotation test','public_key_pem':old_pem,'surface':'browser_overlay'}, headers=ADMIN_AUTH)
    assert registered.status_code == 201, registered.text
    body = json.dumps({'public_key_pem':new_pem}, separators=(',', ':')).encode()
    headers = human_headers('POST','/v1/devices/self/rotate',body,private_key=old_key,device_id=old_id)
    first = client.post('/v1/devices/self/rotate', content=body, headers={'Content-Type':'application/json',**headers})
    assert first.status_code == 200, first.text
    data = first.json()
    assert data['device_id'] == device_id_for_public_key_pem(new_pem)
    assert data['label'] == 'rotation test' and data['state'] == 'active'
    assert data['superseded_by'] is None
    assert data['device_id'] != old_id
    old_view = client.get('/v1/devices', headers=ADMIN_AUTH).json()
    prior = next(x for x in old_view if x['device_id']==old_id)
    assert prior['state']=='rotating' and prior['superseded_by']==data['device_id'] and prior['previous_valid_until']
    body2 = json.dumps({'public_key_pem':new_pem}, separators=(',', ':')).encode()
    headers2 = human_headers('POST','/v1/devices/self/rotate',body2,private_key=old_key,device_id=old_id)
    second = client.post('/v1/devices/self/rotate', content=body2, headers={'Content-Type':'application/json',**headers2})
    assert second.status_code == 200 and second.json()['device_id']==data['device_id']
    _, other_pem = keypair()
    body3 = json.dumps({'public_key_pem':other_pem}, separators=(',', ':')).encode()
    headers3 = human_headers('POST','/v1/devices/self/rotate',body3,private_key=old_key,device_id=old_id)
    conflict = client.post('/v1/devices/self/rotate', content=body3, headers={'Content-Type':'application/json',**headers3})
    assert conflict.status_code == 409


def test_old_key_verifies_in_overlap_then_expires_and_revoked_cannot_rotate(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import models
    from app.db import SessionLocal
    old_key, old_pem = keypair()
    new_key, new_pem = keypair()
    old_id = device_id_for_public_key_pem(old_pem)
    new_id = device_id_for_public_key_pem(new_pem)
    assert client.post('/v1/devices', json={'label': 'overlap', 'public_key_pem': old_pem, 'surface': 'browser_overlay'}, headers=ADMIN_AUTH).status_code == 201
    body = json.dumps({'public_key_pem': new_pem}, separators=(',', ':')).encode()
    rotated = client.post('/v1/devices/self/rotate', content=body, headers={'Content-Type': 'application/json', **human_headers('POST', '/v1/devices/self/rotate', body, private_key=old_key, device_id=old_id)})
    assert rotated.status_code == 200, rotated.text
    # Both keys verify during the overlap.
    for key, dev in ((old_key, old_id), (new_key, new_id)):
        me = client.get('/v1/devices/self', headers=human_headers('GET', '/v1/devices/self', private_key=key, device_id=dev))
        assert me.status_code == 200, me.text
        assert me.json()['device_id'] == dev
    # Successor is a plain active device; a rotate signed by it works too (chain).
    assert client.get('/v1/devices/self', headers=human_headers('GET', '/v1/devices/self', private_key=new_key, device_id=new_id)).json()['state'] == 'active'
    # Overlap over: the old key is refused and the row is revoked at the overlap end.
    with SessionLocal() as db:
        row = db.get(models.Device, old_id)
        row.previous_valid_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    refused = client.get('/v1/devices/self', headers=human_headers('GET', '/v1/devices/self', private_key=old_key, device_id=old_id))
    assert refused.status_code in (401, 403), refused.text
    expired = next(x for x in client.get('/v1/devices', headers=ADMIN_AUTH).json() if x['device_id'] == old_id)
    assert expired['state'] == 'expired' and expired['revoked_at']
    # A revoked device cannot rotate.
    rev_key, rev_pem = keypair()
    rev_id = device_id_for_public_key_pem(rev_pem)
    assert client.post('/v1/devices', json={'label': 'revoked', 'public_key_pem': rev_pem, 'surface': 'browser_overlay'}, headers=ADMIN_AUTH).status_code == 201
    assert client.post(f'/v1/devices/{rev_id}/revoke', headers=ADMIN_AUTH).status_code == 200
    _, another_pem = keypair()
    body = json.dumps({'public_key_pem': another_pem}, separators=(',', ':')).encode()
    denied = client.post('/v1/devices/self/rotate', content=body, headers={'Content-Type': 'application/json', **human_headers('POST', '/v1/devices/self/rotate', body, private_key=rev_key, device_id=rev_id)})
    assert denied.status_code in (401, 403, 409), denied.text


def test_rotate_rejects_bad_pem_and_same_key():
    old_key, old_pem = keypair()
    old_id = device_id_for_public_key_pem(old_pem)
    assert client.post('/v1/devices', json={'label': 'pem', 'public_key_pem': old_pem, 'surface': 'browser_overlay'}, headers=ADMIN_AUTH).status_code == 201
    body = json.dumps({'public_key_pem': '-----BEGIN PUBLIC KEY-----\n' + 'A' * 120 + '\n-----END PUBLIC KEY-----\n'}, separators=(',', ':')).encode()
    bad = client.post('/v1/devices/self/rotate', content=body, headers={'Content-Type': 'application/json', **human_headers('POST', '/v1/devices/self/rotate', body, private_key=old_key, device_id=old_id)})
    assert bad.status_code == 422
    body = json.dumps({'public_key_pem': old_pem}, separators=(',', ':')).encode()
    same = client.post('/v1/devices/self/rotate', content=body, headers={'Content-Type': 'application/json', **human_headers('POST', '/v1/devices/self/rotate', body, private_key=old_key, device_id=old_id)})
    assert same.status_code == 409


def test_rotation_writes_device_events():
    from app import models
    from app.db import SessionLocal
    old_key, old_pem = keypair()
    _, new_pem = keypair()
    old_id = device_id_for_public_key_pem(old_pem)
    assert client.post('/v1/devices', json={'label': 'events', 'public_key_pem': old_pem, 'surface': 'browser_overlay'}, headers=ADMIN_AUTH).status_code == 201
    body = json.dumps({'public_key_pem': new_pem}, separators=(',', ':')).encode()
    assert client.post('/v1/devices/self/rotate', content=body, headers={'Content-Type': 'application/json', **human_headers('POST', '/v1/devices/self/rotate', body, private_key=old_key, device_id=old_id)}).status_code == 200
    with SessionLocal() as db:
        rows = db.query(models.DeviceEvent).filter(models.DeviceEvent.device_id == old_id).all()
        assert [r.action for r in rows] == ['device_rotated']
        assert json.loads(rows[0].detail)['successor_device_id'] == device_id_for_public_key_pem(new_pem)
