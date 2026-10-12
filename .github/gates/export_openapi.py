#!/usr/bin/env python3
"""Export the reviewed API contract from its single authoritative definition."""
import sys
from pathlib import Path
import yaml

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'api'))
from app.main import app  # noqa: E402

(root / 'api/openapi.yaml').write_text(yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True))
print('Exported api/openapi.yaml')
