"""The shared vector file pins the device-signature contract for every client
(extension, macOS app). If this test fails, a client built against the
vectors will be refused by the server, so change both together."""

import base64
import hashlib
import json
import pathlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app import devicesig

VECTORS = pathlib.Path(__file__).parent / "vectors" / "devicesig_v1.json"


def load():
    return json.loads(VECTORS.read_text())


def test_device_id_matches_public_key():
    v = load()
    assert devicesig.device_id_for_public_key_pem(v["public_key_spki_pem"]) == v["device_id"]
    assert v["device_id"].startswith("dev_") and len(v["device_id"]) == 4 + 32


def test_canonical_strings_and_body_hashes():
    v = load()
    for c in v["cases"]:
        body = c["body_utf8"].encode("utf-8")
        assert hashlib.sha256(body).hexdigest() == c["body_sha256_hex"], c["name"]
        canonical = devicesig.canonical_string(c["timestamp"], c["nonce"], c["method"], c["path_and_query"], body)
        assert canonical.decode("utf-8") == c["canonical_utf8"], c["name"]


def test_recorded_signatures_verify():
    v = load()
    for c in v["cases"]:
        canonical = c["canonical_utf8"].encode("utf-8")
        assert devicesig.verify_signature(v["public_key_spki_pem"], canonical, c["signature_b64url"]), c["name"]
        # Tampering with one byte must fail.
        tampered = canonical[:-1] + bytes([canonical[-1] ^ 1])
        assert not devicesig.verify_signature(v["public_key_spki_pem"], tampered, c["signature_b64url"]), c["name"]


def test_fresh_signature_with_test_key_round_trips():
    v = load()
    key = serialization.load_pem_private_key(v["test_private_key_pkcs8_pem"].encode(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    canonical = devicesig.canonical_string("1759600099", "fresh", "POST", "/v1/x", b"{}")
    r, s = decode_dss_signature(key.sign(canonical, ec.ECDSA(hashes.SHA256())))
    sig = base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).rstrip(b"=").decode()
    assert devicesig.verify_signature(v["public_key_spki_pem"], canonical, sig)
