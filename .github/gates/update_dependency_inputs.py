#!/usr/bin/env python3
"""Record direct-input digests after regenerating and reviewing both locks."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("api/requirements.txt", "api/requirements-dev.in")


def main():
    values = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in NAMES}
    (ROOT / ".github/gates/dependency_inputs.json").write_text(json.dumps(values, indent=2) + "\n")
    print("Recorded direct dependency input digests; review these with the regenerated locks")


if __name__ == "__main__":
    main()
