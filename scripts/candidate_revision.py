#!/usr/bin/env python3
"""Find the candidate revision of a Cloud Run service.

Reads the JSON of `gcloud run services describe <service> --format json` from
stdin and prints either the revision name (--name) or the tag URL (--url) of
the traffic target tagged "candidate" (the revision the deploy step created
with --no-traffic --tag=candidate). Exits 1 when no such tag exists, so the
pipeline never checks or smokes the wrong revision.
"""
import argparse
import json
import sys

TAG = "candidate"


def find_candidate(service: dict) -> dict:
    traffic = (service.get("status") or {}).get("traffic") or []
    hits = [t for t in traffic if t.get("tag") == TAG]
    if len(hits) != 1:
        raise SystemExit(f"FAIL candidate_revision: expected one traffic target tagged {TAG!r}, found {len(hits)}")
    target = hits[0]
    if not target.get("revisionName") or not target.get("url"):
        raise SystemExit(f"FAIL candidate_revision: tagged target lacks revisionName or url: {sorted(target)}")
    return target


def main() -> int:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", action="store_true", help="print the candidate revision name")
    group.add_argument("--url", action="store_true", help="print the candidate tag URL")
    args = ap.parse_args()
    try:
        service = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(f"FAIL candidate_revision: service description is not JSON: {exc}")
        return 1
    target = find_candidate(service)
    print(target["revisionName"] if args.name else target["url"].rstrip("/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
