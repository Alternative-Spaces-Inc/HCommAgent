#!/usr/bin/env python3
"""Fail when the live service carries a switch that must stay local
(HCA-P21a Day 2). Reads the JSON of
`gcloud run services describe <service> --region <region> --format json`
from stdin or from the file named as the first argument, and exits 1 when
any container env entry names a forbidden variable.

HCA_WEBHOOK_ALLOW_INSECURE permits http and loopback webhook destinations for
the smoke-local receiver; on the deployed service it would turn the SSRF
rules off. The pipeline runs this right after the deploy step.
"""
import json
import sys

FORBIDDEN = {"HCA_WEBHOOK_ALLOW_INSECURE"}


def main() -> int:
    raw = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    try:
        svc = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"FAIL check_config: service description is not JSON: {exc}")
        return 1
    containers = (((svc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    found = []
    for c in containers:
        for env in c.get("env") or []:
            if env.get("name") in FORBIDDEN:
                found.append(env["name"])
    if found:
        print(f"FAIL check_config: live service env carries {sorted(set(found))}; these are local only switches")
        return 1
    names = sorted({e.get("name") for c in containers for e in (c.get("env") or []) if e.get("name")})
    print(f"OK check_config: {len(containers)} container(s), {len(names)} env vars, no forbidden switch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
