#!/usr/bin/env python3
"""Synthetic decision, signed receiver effect, audit and deduplication over HTTP.

Uses only disposable loopback servers/databases and freshly generated credentials.
The simulated human signer does not exercise production Google authorization.
"""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

ROOT = Path(__file__).resolve().parents[1]


def verify_delivery(secret, header, raw, delivery_id):
    """Independent receiver: verify exact bytes, freshness and signed ID."""
    try:
        payload = json.loads(raw)
        fields = [part.strip().partition("=") for part in header.split(",")]
        timestamps = [v for k, _, v in fields if k == "t"]
        signatures = [v for k, _, v in fields if k == "v1"]
        if len(timestamps) != 1 or not signatures or not isinstance(payload, dict):
            return False
        timestamp = int(timestamps[0])
        if abs(time.time() - timestamp) > 300 or not delivery_id or payload.get("delivery_id") != delivery_id:
            return False
        expected = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
        return any(len(value) == 64 and all(c in "0123456789abcdef" for c in value)
                   and hmac.compare_digest(expected, value) for value in signatures)
    except (ValueError, UnicodeDecodeError, TypeError):
        return False


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    received = queue.Queue()
    signing_secret = [None]
    process = None
    receiver = None
    with tempfile.TemporaryDirectory(prefix="hca-demo-") as temporary:
        ledger = Path(temporary) / "receiver.db"
        with sqlite3.connect(ledger) as db:
            db.execute("CREATE TABLE effects (delivery_id TEXT PRIMARY KEY, campaign TEXT NOT NULL)")
        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400)
                    return
                if not 0 < size <= 1024 * 1024:
                    self.send_error(413)
                    return
                raw = self.rfile.read(size)
                delivery_id = self.headers.get("X-HCA-Delivery-Id", "")
                signature = self.headers.get("X-HCA-Signature", "")
                if not signing_secret[0] or not verify_delivery(signing_secret[0], signature, raw, delivery_id):
                    self.send_error(401)
                    return
                payload = json.loads(raw)
                if (payload.get("answer") or {}).get("value") != "approve":
                    self.send_error(400)
                    return
                # The effect and its dedupe ID are the same committed row.
                # Production receivers must share one transaction across their own effect/ledger.
                with sqlite3.connect(ledger) as db:
                    db.execute("INSERT OR IGNORE INTO effects VALUES (?, ?)",
                               (delivery_id, "Synthetic campaign resumed"))
                received.put((delivery_id, raw, signature))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        try:
            receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
            threading.Thread(target=receiver.serve_forever, daemon=True).start()
            env = {k: v for k, v in os.environ.items() if not k.startswith(("HCA_", "WEBHOOK_", "SWEEP_", "K_"))
                   and k not in {"DATABASE_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}}
            env |= {"HCA_ENV": "development", "DATABASE_URL": "sqlite:///" + temporary + "/api.db",
                    "HCA_ADMIN_TOKEN": secrets.token_urlsafe(32), "SWEEP_TRIGGER_TOKEN": secrets.token_urlsafe(32),
                    "HCA_API_KEY_PEPPER": secrets.token_urlsafe(32), "HCA_REQUIRE_GOOGLE_LINK": "0",
                    "HCA_WEBHOOK_ALLOW_INSECURE": "1", "HCA_JOBS_POLL_SECONDS": ".1",
                    "NO_PROXY": "127.0.0.1,localhost,::1"}
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "api", env=env,
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            port = free_port()
            with open(Path(temporary) / "api.log", "w") as log:
                process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                    "--port", str(port), "--log-level", "warning"], cwd=ROOT / "api", env=env, stdout=log, stderr=log)
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False,
                                  follow_redirects=False) as http:
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            if http.get("/health").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline or process.poll() is not None:
                            raise RuntimeError("disposable demo API did not start")
                        time.sleep(.1)

                    def call(method, path, body=None, headers=None):
                        response = http.request(method, path, json=body, headers=headers)
                        response.raise_for_status()
                        return response

                    admin = {"Authorization": "Bearer " + env["HCA_ADMIN_TOKEN"]}
                    caller = "synthetic-" + secrets.token_hex(8)
                    key = call("POST", "/v1/api-keys", {"caller": caller}, admin).json()["api_key"]
                    agent = {"Authorization": "Bearer " + key}
                    private = ec.generate_private_key(ec.SECP256R1())
                    public = private.public_key()
                    der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                    pem = public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
                    device = "dev_" + hashlib.sha256(der).hexdigest()[:32]
                    call("POST", "/v1/devices", {"label": "Synthetic device", "surface": "browser_overlay", "public_key_pem": pem}, admin)

                    def human(method, path, body=None, idem=None):
                        raw = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
                        timestamp, nonce = str(int(time.time())), secrets.token_urlsafe(12)
                        canonical = "\n".join(["HCA1", timestamp, nonce, method, path, hashlib.sha256(raw).hexdigest()]).encode()
                        signature = private.sign(canonical, ec.ECDSA(hashes.SHA256()))
                        r, s = decode_dss_signature(signature)
                        signature = base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode().rstrip("=")
                        headers = {"X-HCA-Device": device, "X-HCA-Timestamp": timestamp, "X-HCA-Nonce": nonce,
                                   "X-HCA-Signature": signature, "Content-Type": "application/json"}
                        if idem:
                            headers["Idempotency-Key"] = idem
                        response = http.request(method, path, content=raw, headers=headers)
                        response.raise_for_status()
                        return response

                    hook = f"http://127.0.0.1:{receiver.server_port}/synthetic"
                    destination = call("POST", "/v1/webhooks", {"url": hook}, agent).json()
                    signing_secret[0] = destination["secret"]
                    ask = {"caller": caller, "audience": "owner", "title": "Approve a synthetic campaign?",
                           "why": "Mock workflow; no external spend or publication.", "control": "approve_deny",
                           "options_or_fields": [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}],
                           "suggested": {}, "deliver_to": {"mode": "webhook", "url": hook}}
                    request_id = call("POST", "/v1/requests", ask, agent).json()["request_id"]
                    assert any(item["request_id"] == request_id for item in human("GET", "/v1/inbox").json())
                    print("1. Synthetic agent created a decision; the registered signer sees it.")
                    answer = {"control": "approve_deny", "value": "approve", "surface": "browser_overlay"}
                    path = f"/v1/requests/{request_id}/answer"
                    original = human("POST", path, answer, "synthetic-approval")
                    delivery_id, raw, signature = received.get(timeout=15)
                    print("2. Simulated decision produced a verified webhook and one committed mock effect.")
                    audit = human("GET", "/v1/audit?agent=" + caller).json()
                    assert len(audit) == 1 and audit[0]["device"] == device
                    assert audit[0]["answered_by"] == "Synthetic device"
                    print("3. Audit records the authenticated device and decision.")
                    replay = human("POST", path, answer, "synthetic-approval")
                    assert replay.headers.get("Idempotent-Replayed") == "true" and replay.json() == original.json()
                    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as recipient:
                        headers = {"X-HCA-Delivery-Id": delivery_id, "X-HCA-Signature": signature}
                        assert recipient.post(hook, content=raw, headers=headers).status_code == 204
                        headers["X-HCA-Delivery-Id"] = "substituted-id"
                        assert recipient.post(hook, content=raw, headers=headers).status_code == 401
                    with sqlite3.connect(ledger) as db:
                        assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
                    print("4. Decision and delivery retries keep one effect; a substituted delivery ID is rejected.")
                    print("PASS: create -> signed decision -> authenticated effect -> audit -> safe retries.")
                    print("Disposable development simulation; production Google authorization is a separate boundary.")
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if receiver is not None:
                receiver.shutdown()
                receiver.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
