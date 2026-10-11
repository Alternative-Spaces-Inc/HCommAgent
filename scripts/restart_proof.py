#!/usr/bin/env python3
"""HCA-P20 Day 5: restart proof over real HTTP.

Proves the durable delivery contract from the client's side: a device that
loses its SSE stream (because the API restarted, a revision rolled over, or
the client itself went away) receives every inbox event created while it was
gone, exactly once, when it reconnects with the last id it saw.

Steps (each prints PASS or FAIL):
  1. mint an api key (admin bearer), register a temporary P-256 device
  2. open GET /v1/inbox/stream, read until ": connected", record the highest
     id seen (0 when the stream is empty), close the stream
  3. create N asks through POST /v1/requests while nothing is listening
  4. run --gap-cmd if given (a command that restarts the API and waits for
     /healthz), then wait --gap seconds
  5. reconnect with ?since=<id> and collect request.created frames until all N
     request ids are seen or --timeout elapses; assert each exactly once and
     every id above the recorded one; repeat with the Last-Event-ID header
     (the second read is a replay of the same rows and must match)
  6. cancel the asks, revoke the device and the key

Exit code 0 when every check passed, 1 otherwise. The last line is a JSON
summary. Needs HCA_ADMIN_TOKEN in the environment (never printed). Against a
deployment that enforces the Google link (HCA_REQUIRE_GOOGLE_LINK=1) a fresh
device cannot read the stream, so there the proof is run with a linked client
and read from GET /v1/internal/metrics (replay counters); see
docs/deploys/2026-10-11_p20_day4_day5_record.md.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smoke_golden_path import ask_body, http, make_device  # noqa: E402

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} {name}{(': ' + detail) if detail else ''}", flush=True)
    return bool(ok)


def read_stream(base: str, path_and_query: str, headers: dict, want: set, timeout: float, marker: dict = None):
    """Read SSE frames; return (frames seen for the wanted request ids, highest id seen)."""
    req = urllib.request.Request(base + path_and_query, headers={"Accept": "text/event-stream", **headers})
    seen = []
    created = []  # marker mode: every request.created frame, matched once the marker id is known
    highest = 0
    deadline = time.monotonic() + timeout
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        assert resp.status == 200, f"stream returned {resp.status}"
        buf = b""
        cur_id, cur_event, cur_data = None, None, None
        while time.monotonic() < deadline:
            chunk = resp.readline()
            if not chunk:
                break
            buf = chunk
            line = buf.decode().rstrip("\n")
            if line == "":
                if cur_event and cur_data is not None:
                    env = json.loads(cur_data)
                    rid = (env.get("data") or {}).get("request_id")
                    if cur_id is not None:
                        highest = max(highest, int(cur_id))
                    if marker is not None and cur_event == "request.created":
                        # the frame can arrive before the posting thread has parsed its
                        # 201 and filled marker["rid"]; keep every created frame and match
                        # once the id is known (first pipeline run lost the frame this way)
                        created.append((int(cur_id) if cur_id is not None else None, rid))
                        if marker.get("rid") and marker["rid"] in {r for _, r in created}:
                            seen.extend(f for f in created if f[1] == marker["rid"])
                            break
                        continue
                    if cur_event == "request.created" and rid in want:
                        seen.append((int(cur_id) if cur_id is not None else None, rid))
                        if {r for _, r in seen} == want:
                            break
                cur_id, cur_event, cur_data = None, None, None
                continue
            if line.startswith(":"):
                continue
            if line.startswith("id:"):
                cur_id = line[3:].strip()
            elif line.startswith("event:"):
                cur_event = line[6:].strip()
            elif line.startswith("data:"):
                cur_data = line[5:].strip()
    if marker is not None and not seen and marker.get("rid"):
        seen.extend(f for f in created if f[1] == marker["rid"])
    return seen, highest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="API base URL, no trailing slash")
    ap.add_argument("--count", type=int, default=3, help="asks created while disconnected")
    ap.add_argument("--gap", type=float, default=5.0, help="seconds disconnected before reconnecting")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for the replayed frames")
    ap.add_argument("--gap-cmd", default="", help="shell command run during the gap, e.g. one that restarts the API "
                    "and waits for /healthz; its exit code is a check")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    admin = os.environ.get("HCA_ADMIN_TOKEN", "")
    if not admin:
        print("FAIL HCA_ADMIN_TOKEN is not set")
        return 1
    admin_h = {"Authorization": f"Bearer {admin}"}
    caller = f"restart-proof-{int(time.time())}"

    code, body = http("POST", f"{base}/v1/api-keys", {"caller": caller}, admin_h)
    if not check("mint api key", code == 201 and str(body.get("api_key", "")).startswith("hca_"), str(code)):
        return 1
    agent = {"Authorization": f"Bearer {body['api_key']}"}
    key_prefix = body["prefix"]

    pem, sign = make_device()
    code, body = http("POST", f"{base}/v1/devices", {"label": "restart proof device", "public_key_pem": pem}, admin_h)
    if not check("register device", code == 201 and str(body.get("device_id", "")).startswith("dev_"), str(code)):
        return 1
    device_id = body["device_id"]

    def human(method: str, path_and_query: str) -> dict:
        return {"X-HCA-Device": device_id, **sign(method, path_and_query)}

    rids = []
    ok_all = True
    try:
        # 2. baseline: while connected, create one marker ask and receive its
        # frame live; its id is the last id this client saw before the gap.
        import threading
        marker = {}

        def post_marker():
            time.sleep(1.0)
            code, body = http("POST", f"{base}/v1/requests", ask_body(caller, {"mode": "poll"}), agent)
            if code == 201:
                marker["rid"] = body["request_id"]
        threading.Thread(target=post_marker, daemon=True).start()
        try:
            # the thread fills marker["rid"] within a second; read until that id arrives
            seen0, _ = read_stream(base, "/v1/inbox/stream", human("GET", "/v1/inbox/stream"), set(), 20, marker=marker)
        except Exception as e:  # noqa: BLE001
            ok_all &= check("stream connects and delivers a live frame", False, repr(e))
            return 1
        if marker.get("rid"):
            rids.append(marker["rid"])
        baseline = seen0[0][0] if seen0 else None
        if not check("stream connects and delivers a live frame", baseline is not None, f"baseline id {baseline}"):
            return 1

        # 3. create asks while nothing listens
        for i in range(args.count):
            code, body = http("POST", f"{base}/v1/requests", ask_body(caller, {"mode": "poll"}), agent)
            if code == 201:
                rids.append(body["request_id"])
        ok_all &= check("asks created while disconnected", len(rids) == args.count + 1, f"{len(rids) - 1} of {args.count}")
        want = set(rids[1:])

        # 4. the gap: optionally restart the API in the middle of it
        if args.gap_cmd:
            import subprocess
            run = subprocess.run(args.gap_cmd, shell=True, capture_output=True, text=True)
            rc = run.returncode
            tail = (run.stdout + run.stderr).strip().splitlines()[-5:]
            ok_all &= check("gap command (API restart)", rc == 0, f"exit {rc}" + (": " + " | ".join(tail) if rc else ""))
        time.sleep(args.gap)

        # 5a. reconnect with ?since=
        q = f"/v1/inbox/stream?since={baseline}"
        seen, _ = read_stream(base, q, human("GET", q), want, args.timeout)
        got = [r for _, r in seen]
        ok_all &= check("every missed ask replayed (?since=)", set(got) == want, f"{len(got)} of {len(want)}")
        ok_all &= check("each exactly once (?since=)", len(got) == len(set(got)), f"{len(got)} frames")
        ok_all &= check("ids above baseline, ascending", all(i is not None and i > baseline for i, _ in seen)
                        and [i for i, _ in seen] == sorted(i for i, _ in seen), str([i for i, _ in seen]))

        # 5b. reconnect with the Last-Event-ID header: same rows again
        h = human("GET", "/v1/inbox/stream")
        h["Last-Event-ID"] = str(baseline)
        seen2, _ = read_stream(base, "/v1/inbox/stream", h, want, args.timeout)
        ok_all &= check("same replay via Last-Event-ID header", [i for i, _ in seen2] == [i for i, _ in seen],
                        str([i for i, _ in seen2]))
    finally:
        # 6. cleanup
        cancelled = 0
        for rid in rids:
            code, _ = http("POST", f"{base}/v1/requests/{rid}/cancel", {"reason": "restart proof cleanup"}, agent)
            cancelled += code == 200
        check("asks cancelled", cancelled == len(rids), f"{cancelled} of {len(rids)}")
        code, _ = http("POST", f"{base}/v1/devices/{device_id}/revoke", {}, admin_h)
        check("device revoked", code == 200, str(code))
        code, _ = http("POST", f"{base}/v1/api-keys/{key_prefix}/revoke", {}, admin_h)
        check("api key revoked", code == 200, str(code))

    passed = all(r["ok"] for r in RESULTS)
    print(json.dumps({"restart_proof": "PASS" if passed else "FAIL", "asks_missed": max(0, len(rids) - 1),
                      "checks": len(RESULTS), "failed": [r["check"] for r in RESULTS if not r["ok"]]}))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
