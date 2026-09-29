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

def main():
    repo_root = Path(__file__).parents[2]
    manifest_path = repo_root / "bootstrap" / "HCA_MANIFEST.yaml"

    if not manifest_path.is_file():
        print(f"Error: Manifest not found at {manifest_path}")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    skills = data.get("skills", [])
    if not skills:
        print("Warning: No skills found in manifest.")
        sys.exit(0)

    errors = []
    verified_count = 0

    for item in skills:
        rel_path = item.get("path")
        expected_sha = item.get("sha256")
        name = item.get("name")

        if not rel_path or not expected_sha:
            errors.append(f"Invalid manifest entry for skill {name}: path or sha256 missing")
            continue

        file_path = repo_root / rel_path
        if not file_path.is_file():
            errors.append(f"Skill file missing: {rel_path}")
            continue

        actual_sha = compute_sha256(file_path)
        if actual_sha != expected_sha:
            errors.append(f"SHA256 mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}")
        else:
            verified_count += 1

    if errors:
        print("Manifest verification failures:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print(f"OK: Verified {verified_count} skills in manifest successfully.")
        sys.exit(0)

if __name__ == "__main__":
    main()
