#!/usr/bin/env python3
"""Wait until a health URL answers 200, for build steps whose image has no curl
(python:3.12-slim). Usage: wait_healthz.py URL [SECONDS]. Exit 0 on the first
200, 1 when SECONDS pass without one (the last error is printed)."""
import sys
import time
import urllib.error
import urllib.request


def main() -> int:
    url = sys.argv[1]
    budget = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    deadline = time.monotonic() + budget
    last = "no attempt"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    print(f"healthz 200 after {budget - (deadline - time.monotonic()):.1f}s")
                    return 0
                last = f"status {r.status}"
        except (urllib.error.URLError, OSError) as e:
            last = str(e)
        time.sleep(1)
    print(f"wait_healthz: {url} not healthy within {budget:.0f}s, last: {last}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
