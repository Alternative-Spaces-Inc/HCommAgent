#!/usr/bin/env python3
"""check_openapi_parity.py: parity gate between api/openapi.yaml and the schema FastAPI generates.

HCA-P19 Day 3. Paths and methods must match both ways. Success codes must
match exactly. Error codes are compared one way only: every error code the
app declares in its response_model map must appear in the yaml. The yaml may
document more error codes (401, 403, 404, 409, 503 raised through
HTTPException never reach FastAPI's schema) and FastAPI's automatic 422 for
validation is ignored, so the gate checks what the code can prove and the
contract stays the richer document. Known differences live in
openapi_parity_allow.json and shrink as Day 3 closes them."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import yaml

EXCLUDED_PATHS: Set[str] = {"/openapi.json", "/docs", "/redoc", "/health", "/healthz"}
HTTP_METHODS: Set[str] = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check parity between OpenAPI contract and generated schema.")
    parser.add_argument(
        "--yaml-path",
        type=Path,
        default=None,
        help="Path to hand-written openapi.yaml",
    )
    parser.add_argument(
        "--allowlist-path",
        type=Path,
        default=None,
        help="Path to openapi_parity_allow.json",
    )
    return parser.parse_args()


def load_yaml_schema(yaml_path: Path) -> Dict[str, Any]:
    if not yaml_path.is_file():
        print(f"Error: YAML file not found at {yaml_path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        print(f"Error parsing YAML at {yaml_path}: {e}", file=sys.stderr)
        sys.exit(1)


def load_app_schema(repo_root: Path) -> Dict[str, Any]:
    api_dir = repo_root / "api"
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))

    os.environ.setdefault("DATABASE_URL", "sqlite:///./parity.db")
    os.environ.setdefault("HCA_ADMIN_TOKEN", "x")

    try:
        from app.main import app
    except Exception as e:
        print(f"Error importing app.main: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        schema = app.openapi()
        return schema if isinstance(schema, dict) else {}
    except Exception as e:
        print(f"Error generating app OpenAPI schema: {e}", file=sys.stderr)
        sys.exit(1)


def load_allowlist(allowlist_path: Path) -> Set[str]:
    if not allowlist_path.is_file():
        return set()
    try:
        with open(allowlist_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading allowlist file {allowlist_path}: {e}", file=sys.stderr)
        sys.exit(1)

    if isinstance(data, list):
        return set(data)
    if isinstance(data, dict):
        for key in ("allowed_differences", "allowlist", "allowed", "differences"):
            if key in data and isinstance(data[key], list):
                return set(data[key])
        for val in data.values():
            if isinstance(val, list):
                return set(val)
    return set()


def extract_endpoint_map(schema: Dict[str, Any]) -> Dict[Tuple[str, str], List[str]]:
    endpoint_map: Dict[Tuple[str, str], List[str]] = {}
    paths: Dict[str, Any] = schema.get("paths", {})
    if not isinstance(paths, dict):
        return endpoint_map

    for path, path_data in paths.items():
        if path in EXCLUDED_PATHS or not isinstance(path_data, dict):
            continue
        for method, method_data in path_data.items():
            method_lower = method.lower()
            if method_lower in HTTP_METHODS and isinstance(method_data, dict):
                responses: Dict[str, Any] = method_data.get("responses", {})
                status_codes: List[str] = []
                if isinstance(responses, dict):
                    status_codes = sorted([str(code) for code in responses.keys()])
                endpoint_map[(path, method_lower.upper())] = status_codes

    return endpoint_map


def find_differences(
    yaml_endpoints: Dict[Tuple[str, str], List[str]],
    app_endpoints: Dict[Tuple[str, str], List[str]],
) -> List[str]:
    differences: List[str] = []
    all_pairs: Set[Tuple[str, str]] = set(yaml_endpoints.keys()) | set(app_endpoints.keys())

    for path, method in sorted(all_pairs):
        in_yaml = (path, method) in yaml_endpoints
        in_app = (path, method) in app_endpoints

        if in_app and not in_yaml:
            differences.append(f"MISSING_IN_YAML {path} {method}")
        elif in_yaml and not in_app:
            differences.append(f"MISSING_IN_APP {path} {method}")
        else:
            y_codes = set(yaml_endpoints[(path, method)])
            a_codes = set(app_endpoints[(path, method)]) - {"422"}
            y_success = {c for c in y_codes if c.startswith("2")}
            a_success = {c for c in a_codes if c.startswith("2")}
            if y_success != a_success:
                differences.append(f"SUCCESS_DIFF {path} {method} yaml={sorted(y_success)} app={sorted(a_success)}")
            app_only_errors = sorted(c for c in a_codes - y_codes if not c.startswith("2"))
            if app_only_errors:
                differences.append(f"ERROR_MISSING_IN_YAML {path} {method} app={app_only_errors}")

    return differences


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]

    yaml_path = args.yaml_path if args.yaml_path else repo_root / "api" / "openapi.yaml"
    allowlist_path = (
        args.allowlist_path
        if args.allowlist_path
        else repo_root / ".github" / "gates" / "openapi_parity_allow.json"
    )

    yaml_schema = load_yaml_schema(yaml_path)
    app_schema = load_app_schema(repo_root)

    yaml_endpoints = extract_endpoint_map(yaml_schema)
    app_endpoints = extract_endpoint_map(app_schema)

    differences = find_differences(yaml_endpoints, app_endpoints)
    allowlist = load_allowlist(allowlist_path)

    unallowed = [diff for diff in differences if diff not in allowlist]

    if unallowed:
        for diff in unallowed:
            print(diff)
        print(f"FAIL: {len(unallowed)} differences")
        sys.exit(1)

    print("OK: openapi parity")
    sys.exit(0)


if __name__ == "__main__":
    main()
