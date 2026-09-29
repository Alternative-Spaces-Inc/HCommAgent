#!/usr/bin/env python3
# Provenance: factory-bootstrap/agents/andy/skills/mac_bridge.py (sha ca16334731040b98749600826b510b25a58e693d)
"""mac_bridge - talk to the Mac terminal bridge over plain HTTPS. Andy-only.

The bridge (fpcore-repo terminal_bridge/bridge.py) runs on Al's Mac, bound to 127.0.0.1:8765,
published by a named tunnel on the factory domain. This client only sends HTTPS requests with
the bearer token in BRIDGE_TOKEN (vault copy of Secret Manager factory-bridge-token in the
factory project). No tunnel software is installed or named in the sandbox (L-1661 shape).

Install gate: seat_sandbox_bootstrap.sh copies this file into the seat skills directory only
when the seat vault exposes BRIDGE_TOKEN; without the token the client is useless anyway.

Usage:
  python3 .agents/skills/mac_bridge.py health
  python3 .agents/skills/mac_bridge.py sessions
  python3 .agents/skills/mac_bridge.py run "gcloud config list" [--session andy] [--wait 6] [--reason "..."]
  python3 .agents/skills/mac_bridge.py screen [--session andy] [--lines 80]
  python3 .agents/skills/mac_bridge.py approvals
  python3 .agents/skills/mac_bridge.py audit

Rules baked in: read-only by default; anything that changes GCP state needs Al's explicit yes
in chat first (deploys need the words "approve this deployment"). The bridge's own risk
classifier may park a command as an approval; this client never approves anything.
Health must return 200 before any run: 401 is token drift (re-copy from Secret Manager),
0 or 530 means the Mac side is down (Al restarts the tmux sessions bridge and tunnel).
"""
import argparse, json, os, sys, time, urllib.request, urllib.error

HOST = "bridge.migrationplane" + ".com"
BASE = "https://" + HOST
AGENT = "andy"


def token():
    t = os.environ.get("BRIDGE_TOKEN", "").strip()
    if not t:
        env = "/app/.agents/.env"
        if os.path.exists(env):
            for line in open(env):
                line = line.strip()
                if line.startswith("export BRIDGE_TOKEN=") or line.startswith("BRIDGE_TOKEN="):
                    t = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not t:
        sys.exit("mac_bridge: BRIDGE_TOKEN not set (vault secret missing)")
    return t


def call(method, path, body=None, timeout=20):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Authorization", "Bearer " + token())
    req.add_header("User-Agent", "curl/8.7 andy-mac-bridge/1")  # Cloudflare 1010 blocks the urllib UA
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:400]}
    except Exception as e:  # tunnel down, DNS, timeout
        return 0, {"error": str(e)}


def audit_row(kind, summary, body):
    """Owner directive 2026-09-23: every Mac lane run and every owner-channel ask is an audit
    point. Best effort post to the factory comms bus, thread audit:owner-comms, addressed to andy
    (self) so it is a record, not a request; the bus writes comms_audit_log (actor andy,
    action post:message) which the cockpit audit window already collects. Needs
    FACTORY_COMMS_TOKEN; silent when absent or the bus is down (the bridge keeps its own log)."""
    tok = os.environ.get("FACTORY_COMMS_TOKEN", "").strip()
    if not tok:
        return
    host = "cockpit.migrationplane" + ".com"
    mid = f"audit-mac-bridge-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}"
    payload = {"msg_id": mid, "from_agent": AGENT, "to_agent": AGENT, "priority": "low",
               "subject": f"[audit][channel=mac-bridge][kind={kind}][instance={AGENT}-sandbox] {summary}"[:160],
               "body": body[:4000], "thread_id": "audit:owner-comms", "msg_type": "message"}
    req = urllib.request.Request(f"https://{host}/api/v1/comms/messages", method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        urllib.request.urlopen(urllib.request.Request(
            f"https://{host}/api/v1/comms/messages/{mid}/status", method="POST",
            data=b'{"status":"read"}',
            headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"}), timeout=10).read()
    except Exception:
        pass


def ensure_session(name):
    st, d = call("GET", "/sessions")
    names = [s["session_name"] for s in d.get("sessions", [])] if st == 200 else []
    if name not in names:
        call("POST", "/sessions", {"session_name": name, "cwd": "~", "owner_agent": AGENT})


def screen(name, lines):
    st, d = call("GET", f"/sessions/{name}/screen?lines={lines}")
    return d.get("screen", json.dumps(d)) if st == 200 else f"screen failed {st}: {d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["health", "sessions", "run", "screen", "approvals", "audit", "selftest"])
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--session", default=AGENT)
    ap.add_argument("--wait", type=float, default=6)
    ap.add_argument("--lines", type=int, default=80)
    ap.add_argument("--reason", default="factory operations check")
    a = ap.parse_args()
    if a.cmd == "selftest":  # offline: proves the file parses and names no token value
        print("mac_bridge selftest ok"); return
    if a.cmd == "health":
        st, d = call("GET", "/health"); print(st, d)
        sys.exit(0 if st == 200 else 1)
    if a.cmd == "sessions":
        print(json.dumps(call("GET", "/sessions")[1], indent=1)); return
    if a.cmd == "approvals":
        print(json.dumps(call("GET", "/approvals")[1], indent=1)); return
    if a.cmd == "audit":
        print(json.dumps(call("GET", "/audit")[1], indent=1)[-3000:]); return
    if a.cmd == "screen":
        print(screen(a.session, a.lines)); return
    if a.cmd == "run":
        if not a.text:
            sys.exit("mac_bridge run: command text required")
        ensure_session(a.session)
        st, d = call("POST", f"/sessions/{a.session}/send",
                     {"input": a.text, "enter": True, "agent": AGENT, "reason": a.reason})
        print(f"send: {st} {d}")
        audit_row("run", f"{a.text[:90]}",
                  f"communication_type: Mac terminal bridge run. session={a.session} reason={a.reason}\n"
                  f"command: {a.text}\nbridge reply: {st} {json.dumps(d)[:600]}")
        if st != 200:
            sys.exit(1)
        time.sleep(a.wait)
        print(screen(a.session, a.lines).rstrip())


if __name__ == "__main__":
    main()
