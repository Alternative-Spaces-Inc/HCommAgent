#!/usr/bin/env python3
"""Refuse drift between reviewed direct pins and complete hashed locks."""
from pathlib import Path
import hashlib
import json
import re

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]


def requirements(path, locked=False):
    text = path.read_text()
    items = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "--hash")):
            continue
        if line.startswith("-r "):
            items.update(requirements(path.parent / line[3:].strip()))
            continue
        value = Requirement(line.removesuffix("\\").strip())
        pins = list(value.specifier)
        if len(pins) != 1 or pins[0].operator != "==" or "*" in pins[0].version:
            raise ValueError(f"{path.name}: every package must have an exact version")
        items.setdefault(canonicalize_name(value.name), set()).add(pins[0].version)
    if locked:
        for block in re.split(r"\n(?=[A-Za-z])", text):
            if "==" in block and not re.search(r"--hash=sha256:[0-9a-f]{64}", block):
                raise ValueError(f"{path.name}: package lacks a distribution hash")
    return items


def main():
    inputs = json.loads((ROOT / ".github/gates/dependency_inputs.json").read_text())
    for name in ("api/requirements.txt", "api/requirements-dev.in"):
        if inputs.get(name) != hashlib.sha256((ROOT / name).read_bytes()).hexdigest():
            raise SystemExit("FAIL: lock input changed, including extras/markers; regenerate both locks and input digests")
    for source, lock in (("requirements.txt", "requirements.lock"),
                         ("requirements-dev.in", "requirements-dev.lock")):
        direct = requirements(ROOT / "api" / source)
        resolved = requirements(ROOT / "api" / lock, locked=True)
        if any(resolved.get(name) != versions for name, versions in direct.items()):
            raise SystemExit(f"FAIL: {source} changed; regenerate and review {lock}")
    runtime = requirements(ROOT / "api/requirements.lock", locked=True)
    development = requirements(ROOT / "api/requirements-dev.lock", locked=True)
    if any(development.get(name) != versions for name, versions in runtime.items()):
        raise SystemExit("FAIL: runtime and development transitive versions differ")
    print("OK: direct pins match complete hashed locks; runtime/dev versions agree")


if __name__ == "__main__":
    main()
