#!/usr/bin/env python3
"""HCA golden-path smoke test (HCA-P9, plan Day 10).

Exercises the API over real HTTP the way the agents and the extension do,
not through a TestClient. Two modes:

  --human     full path: mint an agent key, register a P-256 test device,
              post an ask with a webhook, read the inbox signed, answer
              signed, confirm the webhook arrived, read the audit row.
              Needs HCA_REQUIRE_GOOGLE_LINK=0 on the server (a test device
              has no Google account), so it runs against a local uvicorn
              inside the build, before the image is built.
  (default)   agent path only: healthz, mint a key, post an ask, read it
              back, cancel it, read it back cancelled. Runs against the
              live service after deploy; the Google-link gate stays on.

Every check prints one line, PASS or FAIL, and the script exits non-zero
on the first failure. Budget: the whole run must finish in 300 seconds.

Usage:
  python3 scripts/smoke_golden_path.py --base http://127.0.0.1:8080 --human
  python3 scripts/smoke_golden_path.py --base https://<service-url>
The admin token is read from the env var HCA_ADMIN_TOKEN, never from argv.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

START = time.monotonic()
BUDGET_SECONDS = 300
PREFIX = "HCA1"


def elapsed() -> str:
    return f"{time.monotonic() - START:6.1f}s"


def check(name: str, ok: bool, detail: str = ""):
    print(f"[{elapsed()}] {'PASS' if ok else 'FAIL'} {name}{(': ' + detail) if detail else ''}", flush=True)
    if not ok:
        sys.exit(1)
    if time.monotonic() - START > BUDGET_SECONDS:
        print(f"[{elapsed()}] FAIL budget: over {BUDGET_SECONDS}s", flush=True)
        sys.exit(1)


def http(method: str, url: str, body=None, headers=None, raw: bytes = None):
    data = raw if raw is not None else (None if body is None else json.dumps(body, separators=(",", ":")).encode())
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None and "Content-Type" not in (headers or {}):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text)
        except Exception:
            return e.code, text


# ---- signed human calls (mirrors extension/background.js and tests/deviceauth.py)

def make_device():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def sign(method: str, path_and_query: str, body: bytes = b"") -> dict:
        ts = str(int(time.time()))
        nonce = secrets.token_urlsafe(12)
        canonical = "\n".join([PREFIX, ts, nonce, method.upper(), path_and_query,
                               hashlib.sha256(body or b"").hexdigest()]).encode()
        der = private.sign(canonical, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        sig = base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode().rstrip("=")
        return {"X-HCA-Timestamp": ts, "X-HCA-Nonce": nonce, "X-HCA-Signature": sig}

    return pem, sign


# ---- webhook receiver for --human

class _Hook(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(n) if n else b""
        _Hook.received.append({"headers": dict(self.headers), "body": json.loads(body or b"{}")})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def start_hook() -> str:
    srv = HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/hook"


def ask_body(caller: str, deliver_to: dict) -> dict:
    return {
        "caller": caller,
        "audience": "owner",
        "title": "Smoke: golden path",
        "why": "Automated smoke test; no action is taken on the answer.",
        "control": "radio",
        "options_or_fields": [
            {"id": "a", "label": "Option A", "description": "the smoke answer"},
            {"id": "b", "label": "Option B"},
        ],
        "suggested": {"option_id": "a", "why": "the script answers a"},
        "expiry_policy": "expire",
        "deliver_to": deliver_to,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="API base URL, no trailing slash")
    ap.add_argument("--human", action="store_true", help="also run the signed device path and webhook")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    admin = os.environ.get("HCA_ADMIN_TOKEN", "")
    check("env HCA_ADMIN_TOKEN present", bool(admin))

    code, body = http("GET", f"{base}/healthz")
    check("GET /healthz", code == 200, f"{code}")

    caller = f"smoke-{int(time.time())}"
    code, body = http("POST", f"{base}/v1/api-keys", {"caller": caller}, {"Authorization": f"Bearer {admin}"})
    check("POST /v1/api-keys", code == 201 and isinstance(body, dict) and body.get("api_key", "").startswith("hca_"), f"{code}")
    agent = {"Authorization": f"Bearer {body['api_key']}"}

    if not args.human:
        code, body = http("POST", f"{base}/v1/requests", ask_body(caller, {"mode": "poll"}), agent)
        check("POST /v1/requests", code == 201 and body.get("state") == "queued", f"{code} {body if code != 201 else body['state']}")
        rid = body["request_id"]
        code, body = http("GET", f"{base}/v1/requests/{rid}", headers=agent)
        check("GET /v1/requests/{id}", code == 200 and body.get("request_id") == rid and body["request"]["caller"] == caller, f"{code}")
        code, body = http("POST", f"{base}/v1/requests/{rid}/cancel", {"reason": "smoke test cleanup"}, agent)
        check("POST /v1/requests/{id}/cancel", code == 200 and body.get("state") == "cancelled", f"{code}")
        code, body = http("GET", f"{base}/v1/requests/{rid}", headers=agent)
        check("GET after cancel", code == 200 and body.get("state") == "cancelled" and (body.get("answer") or {}).get("value", {}).get("decision") == "cancel", f"{code}")
        print(f"[{elapsed()}] OK agent path, request {rid}", flush=True)
        return

    pem, sign = make_device()
    code, body = http("POST", f"{base}/v1/devices", {"label": "smoke device", "public_key_pem": pem}, {"Authorization": f"Bearer {admin}"})
    check("POST /v1/devices", code == 201 and body.get("device_id", "").startswith("dev_"), f"{code}")
    device_id = body["device_id"]
    human = lambda m, p, b=b"": {"X-HCA-Device": device_id, **sign(m, p, b)}

    hook_url = start_hook()
    code, body = http("POST", f"{base}/v1/requests", ask_body(caller, {"mode": "webhook", "url": hook_url}), agent)
    check("POST /v1/requests (webhook)", code == 201 and body.get("state") == "queued", f"{code}")
    rid = body["request_id"]

    code, body = http("GET", f"{base}/v1/inbox", headers=human("GET", "/v1/inbox"))
    check("GET /v1/inbox signed", code == 200 and isinstance(body, list), f"{code}")
    mine = [i for i in body if i.get("request_id") == rid]
    check("inbox item carries request_id (PR 20 regression)", len(mine) == 1 and mine[0].get("title") == "Smoke: golden path")

    answer = json.dumps({"control": "radio", "value": "a", "surface": "browser_overlay",
                         "path_taken": ["smoke"], "duration_ms": 1}, separators=(",", ":")).encode()
    path = f"/v1/requests/{rid}/answer"
    code, body = http("POST", f"{base}{path}", raw=answer, headers={**human("POST", path, answer), "Content-Type": "application/json"})
    check("POST answer signed", code == 201 and body.get("value") == "a" and body.get("device") == device_id, f"{code} {body if code != 201 else ''}")

    code, body = http("POST", f"{base}{path}", raw=answer, headers={**human("POST", path, answer), "Content-Type": "application/json"})
    check("second answer refused", code == 409, f"{code}")

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not _Hook.received:
        time.sleep(0.2)
    got = _Hook.received[0] if _Hook.received else None
    check("webhook delivered", got is not None and got["headers"].get("X-HCA-Event") == "request.answered"
          and got["headers"].get("X-HCA-Request-Id") == rid, "none within 20s" if got is None else "")

    q = "/v1/audit?" + "agent=" + caller
    code, body = http("GET", f"{base}{q}", headers=human("GET", q))
    check("GET /v1/audit signed", code == 200 and any(r.get("request_id") == rid and r.get("device") == device_id for r in body), f"{code}")

    code, body = http("GET", f"{base}/v1/inbox", headers={"X-HCA-Device": device_id, "X-HCA-Timestamp": str(int(time.time())),
                                                         "X-HCA-Nonce": "x", "X-HCA-Signature": "bad"})
    check("bad signature refused", code in (401, 403), f"{code}")

    code, body = http("POST", f"{base}/v1/devices/{device_id}/revoke", {}, {"Authorization": f"Bearer {admin}"})
    check("revoke smoke device", code == 200 and body.get("revoked_at"), f"{code}")
    print(f"[{elapsed()}] OK golden path, request {rid}, device {device_id}", flush=True)


if __name__ == "__main__":
    main()
