#!/usr/bin/env python3
"""Fail closed when a Cloud Run revision (or service) is not configured the
way production requires.

Reads the JSON of `gcloud run revisions describe <revision> --format json`
(or `gcloud run services describe`) from stdin or from the file named as the
first positional argument. Exit 1 when:

* no container is present,
* a required secret is not mounted from Secret Manager,
* a required environment variable is missing or empty,
* HCA_REQUIRE_GOOGLE_LINK is not "1",
* any forbidden local only switch is present (HCA_WEBHOOK_ALLOW_INSECURE
  permits http and loopback webhook destinations for smoke-local; live it
  would turn the SSRF rules off), or any variable whose name contains LOCAL
  or DEV,
* --expect-commit is given and the commit label differs.

The pipeline runs this on the candidate revision before it is smoked and
before any traffic reaches it.
"""
import argparse
import json
import sys

FORBIDDEN = {"HCA_WEBHOOK_ALLOW_INSECURE"}
FORBIDDEN_NAME_PARTS = ("LOCAL", "DEV")
REQUIRED_SECRETS = {"DATABASE_URL", "HCA_ADMIN_TOKEN", "HCA_API_KEY_PEPPER", "SWEEP_TRIGGER_TOKEN"}
REQUIRED_ENV = {"HCA_GOOGLE_CLIENT_IDS", "HCA_OWNER_EMAILS", "HCA_REQUIRE_GOOGLE_LINK"}


def containers_of(obj: dict) -> list:
    spec = obj.get("spec") or {}
    # Revision: spec.containers. Service: spec.template.spec.containers.
    if "containers" in spec:
        return spec.get("containers") or []
    return (((spec.get("template") or {}).get("spec") or {}).get("containers")) or []


def labels_of(obj: dict) -> dict:
    meta = obj.get("metadata") or {}
    labels = dict(meta.get("labels") or {})
    template_meta = ((obj.get("spec") or {}).get("template") or {}).get("metadata") or {}
    labels.update(template_meta.get("labels") or {})
    return labels


def check(obj: dict, expect_commit: str | None) -> list[str]:
    problems: list[str] = []
    containers = containers_of(obj)
    if not containers:
        return ["no container in the description"]
    plain: dict[str, str] = {}
    secrets: set[str] = set()
    for c in containers:
        for env in c.get("env") or []:
            name = env.get("name")
            if not name:
                continue
            if (env.get("valueFrom") or {}).get("secretKeyRef"):
                secrets.add(name)
            else:
                plain[name] = env.get("value") or ""
    names = set(plain) | secrets
    for name in sorted(names):
        if name in FORBIDDEN:
            problems.append(f"forbidden local only switch present: {name}")
        elif any(part in name for part in FORBIDDEN_NAME_PARTS):
            problems.append(f"variable name suggests a development switch: {name}")
    for name in sorted(REQUIRED_SECRETS - secrets):
        problems.append(f"required secret not mounted from Secret Manager: {name}")
    for name in sorted(REQUIRED_ENV):
        if name not in plain:
            problems.append(f"required env var missing: {name}")
        elif not plain[name].strip():
            problems.append(f"required env var empty: {name}")
    if plain.get("HCA_REQUIRE_GOOGLE_LINK", "").strip() != "1":
        problems.append("HCA_REQUIRE_GOOGLE_LINK must be 1 on a deployed revision")
    if expect_commit:
        got = labels_of(obj).get("commit")
        if got != expect_commit:
            problems.append(f"commit label {got!r} does not match the build commit")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="JSON file; stdin when omitted")
    ap.add_argument("--expect-commit", default=None, help="commit sha the revision must be labelled with")
    args = ap.parse_args()
    if args.path:
        with open(args.path) as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"FAIL check_config: description is not JSON: {exc}")
        return 1
    problems = check(obj, args.expect_commit)
    if problems:
        print("FAIL check_config:")
        for p in problems:
            print(f"  {p}")
        return 1
    containers = containers_of(obj)
    names = sorted({e.get("name") for c in containers for e in (c.get("env") or []) if e.get("name")})
    print(f"OK check_config: {len(containers)} container(s), {len(names)} env vars, required settings present, no forbidden switch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
