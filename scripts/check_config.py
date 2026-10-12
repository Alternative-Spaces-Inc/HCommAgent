#!/usr/bin/env python3
"""Validate one application container and exact candidate identity, without reading secret values."""
import argparse
import json
import os
import re
import sys

FORBIDDEN = {"HCA_WEBHOOK_ALLOW_INSECURE"}
DEVELOPMENT_NAME = re.compile(r"(?:^|_)(?:LOCAL|DEV)(?:_|$)")
OPTIONAL_SECRET_ENV = {"HCA_WEBHOOK_ENCRYPTION_KEYS", "HCA_API_KEY_PREVIOUS_PEPPERS"}
CURRENT_KEY_ID = "HCA_WEBHOOK_CURRENT_KEY_ID"
REQUIRED_SECRETS = {"DATABASE_URL", "HCA_ADMIN_TOKEN", "HCA_API_KEY_PEPPER", "SWEEP_TRIGGER_TOKEN"}
REQUIRED_ENV = {"HCA_OWNER_EMAILS", "HCA_REQUIRE_GOOGLE_LINK"}
DIGEST_IMAGE = re.compile(r"^[a-z0-9.-]+(?:/[A-Za-z0-9._-]+)+@sha256:[0-9a-f]{64}$")
CAPACITY_DEFAULTS = {
    "HCA_MAX_INSTANCES": "2", "HCA_DB_POOL_SIZE": "2", "HCA_DB_MAX_OVERFLOW": "1",
    "HCA_DB_CONNECTION_BUDGET": "25", "HCA_DB_OLD_REVISION_RESERVE": "15",
    "HCA_DB_MIGRATION_RESERVE": "2",
}


def capacity_plan(values):
    parsed = {}
    for name in CAPACITY_DEFAULTS:
        value = str(values.get(name, ""))
        if not re.fullmatch(r"[1-9][0-9]{0,3}", value):
            raise ValueError(name + " must be an integer from 1 to 9999")
        parsed[name] = int(value)
    new_revision = parsed["HCA_MAX_INSTANCES"] * (parsed["HCA_DB_POOL_SIZE"] + parsed["HCA_DB_MAX_OVERFLOW"] + 1)
    old = parsed["HCA_DB_OLD_REVISION_RESERVE"]
    migrations = parsed["HCA_DB_MIGRATION_RESERVE"]
    budget = parsed["HCA_DB_CONNECTION_BUDGET"]
    if new_revision + old + migrations > budget:
        raise ValueError("new revision plus old/draining and migration reserves exceeds the application database connection budget")
    return {"new_revision": new_revision, "old_revision_reserve": old,
            "migration_reserve": migrations, "total": new_revision + old + migrations, "budget": budget}


def containers_of(obj: dict) -> list:
    spec = obj.get("spec") or {}
    if "containers" in spec:
        return spec.get("containers") or []
    return (((spec.get("template") or {}).get("spec") or {}).get("containers")) or []


def labels_of(obj: dict) -> dict:
    labels = dict((obj.get("metadata") or {}).get("labels") or {})
    labels.update((((obj.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels") or {})
    return labels


def check(obj: dict, expect_commit: str | None = None, expect_build: str | None = None,
          expect_image: str | None = None, expect_revision: str | None = None) -> list[str]:
    if not isinstance(obj, dict):
        return ["description must be an object"]
    containers = containers_of(obj)
    if not containers:
        return ["no container in the description"]
    if len(containers) != 1 or not isinstance(containers[0], dict):
        return ["exactly one application container is required; sidecar settings cannot satisfy its configuration"]
    problems = []
    plain, secrets, seen = {}, set(), set()
    for env in containers[0].get("env") or []:
        if not isinstance(env, dict) or not isinstance(env.get("name"), str):
            problems.append("invalid environment entry")
            continue
        name = env["name"]
        if name in seen:
            problems.append(f"duplicate environment entry: {name}")
        seen.add(name)
        reference = (env.get("valueFrom") or {}).get("secretKeyRef")
        if reference:
            if isinstance(reference, dict) and reference.get("name") and reference.get("key"):
                secrets.add(name)
            else:
                problems.append(f"incomplete Secret Manager reference: {name}")
        else:
            plain[name] = str(env.get("value") or "")
    for name in sorted(seen):
        if name in FORBIDDEN:
            problems.append(f"forbidden local only switch present: {name}")
        elif DEVELOPMENT_NAME.search(name):
            problems.append(f"variable name suggests a development switch: {name}")
    for name in sorted(REQUIRED_SECRETS - secrets):
        problems.append(f"required secret not mounted from Secret Manager: {name}")
    for name in sorted(OPTIONAL_SECRET_ENV & seen):
        if name not in secrets or name in plain:
            problems.append(f"optional sensitive configuration must use Secret Manager: {name}")
    keyring_present = "HCA_WEBHOOK_ENCRYPTION_KEYS" in seen
    key_id_present = CURRENT_KEY_ID in seen
    if keyring_present != key_id_present:
        problems.append("encryption keyring and current key ID must be configured together")
    if CURRENT_KEY_ID in plain and not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", plain[CURRENT_KEY_ID]):
        problems.append("current encryption key ID must be a nonempty valid identifier")
    for name in sorted(REQUIRED_ENV):
        if name not in plain:
            problems.append(f"required env var missing: {name}")
        elif not plain[name].strip():
            problems.append(f"required env var empty: {name}")
    client_ids = [value.strip() for name in ("HCA_GOOGLE_CLIENT_ID", "HCA_GOOGLE_CLIENT_IDS")
                  for value in plain.get(name, "").split(",") if value.strip()]
    if not client_ids:
        problems.append("at least one Google OAuth client id is required")
    if plain.get("HCA_REQUIRE_GOOGLE_LINK", "").strip() != "1":
        problems.append("HCA_REQUIRE_GOOGLE_LINK must be 1 on a deployed revision")
    if plain.get("HCA_ENV", "production") != "production":
        problems.append("HCA_ENV must be production on a managed revision")
    try:
        capacity_plan(plain)
    except ValueError as exc:
        problems.append(str(exc))
    annotations = (obj.get("metadata") or {}).get("annotations") or {}
    if expect_revision and annotations.get("autoscaling.knative.dev/maxScale") != plain.get("HCA_MAX_INSTANCES"):
        problems.append("revision maxScale does not match the declared maximum instances")
    labels = labels_of(obj)
    for name, expected in (("commit", expect_commit), ("build", expect_build)):
        if expected and labels.get(name) != expected:
            problems.append(f"{name} label does not match the build")
    if expect_revision and (obj.get("metadata") or {}).get("name") != expect_revision:
        problems.append("revision name does not match the candidate snapshot")
    if expect_image:
        if not DIGEST_IMAGE.fullmatch(expect_image):
            problems.append("expected image must have an immutable 64-hex SHA256 digest")
        if containers[0].get("image") != expect_image:
            problems.append("container image does not match the expected digest")
        resolved = (obj.get("status") or {}).get("imageDigest")
        if resolved and resolved not in {expect_image, expect_image.rsplit("@", 1)[-1]}:
            problems.append("resolved image digest differs from the build")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="revision JSON file, or stdin")
    parser.add_argument("--capacity-from-env", action="store_true", help="check the declared connection budget before cloud mutations")
    for field in ("commit", "build", "image", "revision"):
        parser.add_argument("--expect-" + field)
    args = parser.parse_args()
    try:
        if args.capacity_from_env:
            try:
                plan = capacity_plan(os.environ)
            except ValueError as exc:
                print("FAIL capacity: " + str(exc), file=sys.stderr)
                return 1
            print("OK capacity: " + json.dumps(plan, sort_keys=True))
            return 0
        if args.path:
            with open(args.path) as source:
                obj = json.load(source)
        else:
            obj = json.load(sys.stdin)
        problems = check(obj, args.expect_commit, args.expect_build, args.expect_image, args.expect_revision)
    except (OSError, ValueError, TypeError, AttributeError):
        print("FAIL check_config: invalid revision description", file=sys.stderr)
        return 1
    if problems:
        for problem in problems:
            print("FAIL check_config: " + problem)
        return 1
    print("OK check_config: application settings and expected candidate identity verified; secret values not read")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
