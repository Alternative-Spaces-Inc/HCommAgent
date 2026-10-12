#!/usr/bin/env python3
"""Compare the complete saved and served API contracts with no exceptions.

Object key order is immaterial; routes, schemas, bounds, required fields,
request/response media types, status codes, authentication and headers must
all match. A change requires an explicit export and review of the contract.
Importing the application only builds its schema; no startup hooks or database
queries run. The local fallback uses memory if DATABASE_URL is not configured.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_app_schema(repo_root=ROOT):
    sys.path.insert(0, str(repo_root / "api"))
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    from app.main import app
    return app.openapi()


def schema_differences(saved, generated, path=""):
    """Report JSON pointers for every difference, including nested contracts."""
    if type(saved) is not type(generated):
        return [path or "/"]
    if isinstance(saved, dict):
        differences = []
        for key in sorted(set(saved) | set(generated)):
            pointer = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if key not in saved or key not in generated:
                differences.append(pointer)
            else:
                differences.extend(schema_differences(saved[key], generated[key], pointer))
        return differences
    if isinstance(saved, list):
        if len(saved) != len(generated):
            return [path or "/"]
        return [pointer for i, (left, right) in enumerate(zip(saved, generated))
                for pointer in schema_differences(left, right, path + "/" + str(i))]
    return [] if saved == generated else [path or "/"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yaml-path", type=Path, default=ROOT / "api/openapi.yaml")
    parser.add_argument("--allowlist-path", type=Path, default=ROOT / ".github/gates/openapi_parity_allow.json")
    args = parser.parse_args()
    try:
        allowlist = json.loads(args.allowlist_path.read_text())
        if not isinstance(allowlist, dict) or allowlist.get("allowed_differences") != []:
            raise ValueError("full contract parity permits no allowlisted differences")
        saved = yaml.safe_load(args.yaml_path.read_text())
        generated = load_app_schema()
        differences = schema_differences(saved, generated)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL: cannot check OpenAPI contract: {exc}", file=sys.stderr)
        return 1
    if differences:
        print(f"FAIL: {len(differences)} OpenAPI contract difference(s)")
        for pointer in differences[:25]:
            print(pointer)
        print("Run python .github/gates/export_openapi.py and review api/openapi.yaml")
        return 1
    print("OK: full OpenAPI route, schema, constraint, security, body and header parity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
