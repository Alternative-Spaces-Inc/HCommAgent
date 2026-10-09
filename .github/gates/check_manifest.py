#!/usr/bin/env python3
"""check_manifest.py - Verify skills in bootstrap/HCA_MANIFEST.yaml exist and sha256 match."""

import hashlib
import sys
from pathlib import Path

import yaml


def compute_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def fail(lines):
    print("Manifest verification failures:")
    for line in lines:
        print(f"  - {line}")
    sys.exit(1)


def main():
    repo_root = Path(__file__).parents[2]
    manifest_path = repo_root / "bootstrap" / "HCA_MANIFEST.yaml"
    rel_manifest = manifest_path.relative_to(repo_root)

    if not manifest_path.is_file():
        fail([f"Manifest not found at {rel_manifest}"])

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        fail([f"Invalid YAML in {rel_manifest}: {e}"])

    if not isinstance(data, dict):
        fail([f"{rel_manifest} is empty or not a mapping at the top level"])

    skills = data.get("skills")
    if not isinstance(skills, list) or not skills:
        fail([f"{rel_manifest} has no non-empty 'skills' list"])

    errors = []
    verified_count = 0

    for idx, item in enumerate(skills):
        if not isinstance(item, dict):
            errors.append(f"skills[{idx}] is not a mapping: {item!r}")
            continue

        rel_path = item.get("path")
        expected_sha = item.get("sha256")
        name = item.get("name", f"skills[{idx}]")

        if not rel_path or not expected_sha:
            errors.append(f"Invalid manifest entry for skill {name}: path or sha256 missing")
            continue

        file_path = repo_root / rel_path
        if not file_path.is_file():
            errors.append(f"Skill file missing: {rel_path}")
            continue

        actual_sha = compute_sha256(file_path)
        if actual_sha != expected_sha:
            errors.append(
                f"SHA256 mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}. "
                f"Fix: set sha256 for {name} in {rel_manifest} to the 'got' value "
                f"(shasum -a 256 {rel_path} prints it)."
            )
        else:
            verified_count += 1

    if errors:
        fail(errors)
    print(f"OK: Verified {verified_count} skills in manifest successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
