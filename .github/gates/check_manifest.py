#!/usr/bin/env python3
"""Keep privileged personal operations outside the product repository.

The historical job name is retained to preserve existing branch protection.
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
forbidden = [name for name in ("bootstrap", "platform", "agents") if (root / name).exists()]
if forbidden:
    print("FAIL: operations-only paths in product: " + ", ".join(forbidden))
    sys.exit(1)
print("OK: product tree contains no privileged personal-agent tooling")
