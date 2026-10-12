#!/usr/bin/env python3
"""Export the served API contract from its single authoritative definition.

Run from any directory with the API's dependencies installed. The exporter
starts no application hooks, database connections or credential workflows.
"""
import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_openapi_parity import ROOT, load_app_schema  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-path", type=Path, default=ROOT / "api/openapi.yaml")
    args = parser.parse_args()
    args.output_path.write_text(yaml.safe_dump(load_app_schema(), sort_keys=False, allow_unicode=True))
    print(f"Exported {args.output_path}")


if __name__ == "__main__":
    main()
