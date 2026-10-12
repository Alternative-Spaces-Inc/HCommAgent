#!/usr/bin/env python3
"""Check the entire generated contract: schemas, constraints, auth, headers and routes."""
import json
import sys
from pathlib import Path
import yaml

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'api'))
from app.main import app  # noqa: E402


def main():
    saved = yaml.safe_load((root / 'api/openapi.yaml').read_text())
    generated = app.openapi()
    if saved != generated:
        print('FAIL: api/openapi.yaml differs from the generated contract')
        print('Run python .github/gates/export_openapi.py and review the contract diff')
        before = set((saved.get('paths') or {}).keys())
        after = set(generated['paths'].keys())
        print(json.dumps({'missing_routes': sorted(after-before), 'removed_routes': sorted(before-after)}))
        return 1
    print('OK: full OpenAPI schema and security parity')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
