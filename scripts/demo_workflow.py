#!/usr/bin/env python3
"""Credential-free synthetic create/approve/resume/audit workflow over real HTTP.

Starts its own loopback API, database and signed-webhook receiver in a temporary
directory. Google sign-in is disabled ONLY in this disposable development demo.
No existing deployment, account, database or credentials are accessed.
"""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "api"))
from app.devicesig import canonical_string, device_id_for_public_key_pem  # noqa: E402
from app.webhooks import verify_signature  # noqa: E402


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    received = queue.Queue()
    signing_secret = [None]
    resumed = set()
    artifacts = []
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if size > 1024 * 1024:
                self.send_error(413)
                return
            raw = self.rfile.read(size)
            delivery_id = self.headers.get("X-HCA-Delivery-Id", "")
            if not signing_secret[0] or not verify_signature(signing_secret[0], self.headers.get("X-HCA-Signature", ""),
                                                           raw, delivery_id=delivery_id):
                self.send_error(401)
                return
            payload = json.loads(raw)
            if (payload.get("answer") or {}).get("value") != "approve":
                self.send_error(400)
                return
            if delivery_id not in resumed:
                resumed.add(delivery_id)
                artifacts.append({"campaign": "Synthetic summer campaign", "status": "approved"})
            received.put((delivery_id, payload))
            self.send_response(204)
            self.end_headers()
        def log_message(self, *args):
            pass
    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    worker = threading.Thread(target=receiver.serve_forever, daemon=True)
    worker.start()
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="hca-demo-") as temporary:
            env = os.environ | {"HCA_ENV": "development", "DATABASE_URL": "sqlite:///" + temporary + "/demo.db",
                "HCA_ADMIN_TOKEN": secrets.token_urlsafe(32), "HCA_API_KEY_PEPPER": secrets.token_urlsafe(32),
                "HCA_REQUIRE_GOOGLE_LINK": "0", "HCA_WEBHOOK_ALLOW_INSECURE": "1", "HCA_JOBS_POLL_SECONDS": ".1"}
            # Demo does not inherit production keyring references.
            env.pop("HCA_WEBHOOK_ENCRYPTION_KEYS", None)
            env.pop("HCA_WEBHOOK_CURRENT_KEY_ID", None)
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root / "api", env=env,
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            port = free_port()
            with open(Path(temporary) / "api.log", "w") as log:
                process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                    "--port", str(port), "--log-level", "warning"], cwd=root / "api", env=env, stdout=log, stderr=log)
                base = f"http://127.0.0.1:{port}"
                with httpx.Client(base_url=base, timeout=10, trust_env=False, follow_redirects=False) as http:
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            if http.get("/health").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline or process.poll() is not None:
                            raise RuntimeError("demo API did not start")
                        time.sleep(.1)
                    admin = {"Authorization": "Bearer " + env["HCA_ADMIN_TOKEN"]}
                    def call(method, path, body=None, headers=None):
                        response = http.request(method, path, json=body, headers=headers)
                        response.raise_for_status()
                        return response
                    caller = "demo-" + secrets.token_hex(8)
                    key = call("POST", "/v1/api-keys", {"caller": caller}, admin).json()["api_key"]
                    agent = {"Authorization": "Bearer " + key}
                    private = ec.generate_private_key(ec.SECP256R1())
                    pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
                    device = device_id_for_public_key_pem(pem)
                    call("POST", "/v1/devices", {"label": "Synthetic reviewer", "public_key_pem": pem}, admin)
                    def human(method, path, body=None, idem=None):
                        raw = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
                        timestamp = str(int(time.time()))
                        nonce = secrets.token_urlsafe(12)
                        signature = private.sign(canonical_string(timestamp, nonce, method, path, raw), ec.ECDSA(hashes.SHA256()))
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
                    ask = {"caller": caller, "audience": "owner", "title": "Approve synthetic campaign?",
                           "why": "Mock retail workflow; no external spend or publication.", "control": "approve_deny",
                           "options_or_fields": [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}],
                           "suggested": {}, "deliver_to": {"mode": "webhook", "url": hook}}
                    request_id = call("POST", "/v1/requests", ask, agent).json()["request_id"]
                    assert any(item["request_id"] == request_id for item in human("GET", "/v1/inbox").json())
                    print("1. Agent created a synthetic decision; authorized device sees it.")
                    answer = {"control": "approve_deny", "value": "approve", "surface": "browser_overlay"}
                    path = f"/v1/requests/{request_id}/answer"
                    human("POST", path, answer, "synthetic-approval")
                    delivery_id, payload = received.get(timeout=15)
                    assert payload["delivery_id"] == delivery_id and len(artifacts) == 1
                    print("2. Simulated human approved; signed webhook resumed the mock campaign once.")
                    replay = human("POST", path, answer, "synthetic-approval")
                    assert replay.headers.get("Idempotent-Replayed") == "true"
                    time.sleep(.3)
                    assert len(artifacts) == 1
                    print("3. Retrying the answer returned the original result; no second external effect.")
                    audit = human("GET", "/v1/audit?agent=" + caller).json()
                    assert len(audit) == 1 and audit[0]["device"] == device
                    assert audit[0]["answered_by"] == "Synthetic reviewer"
                    print("4. Audit contains the verified device identity and decision.")
                    print("PASS: create -> decision -> authenticated delivery -> resume -> retry -> audit.")
                    print("Development simulation only; real Google sign-in and client distribution require separate live verification.")
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        receiver.shutdown()
        receiver.server_close()


if __name__ == "__main__":
    main()
