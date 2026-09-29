#!/usr/bin/env python3
"""check_no_dashes.py - Scan tracked text files for em-dash (U+2014) and en-dash (U+2013)."""

import subprocess
import sys
from pathlib import Path

def get_tracked_files():
    try:
        res = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
        return [f for f in res.stdout.splitlines() if f.strip()]
    except Exception as e:
        print(f"Error running git ls-files: {e}")
        sys.exit(1)

def main():
    files = get_tracked_files()
    forbidden = ['\u2014', '\u2013']
    errors = []

    for rel_path in files:
        path = Path(rel_path)
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line_idx, line in enumerate(f, start=1):
                    for char in forbidden:
                        if char in line:
                            errors.append(f"{rel_path}:{line_idx}")
        except Exception:
            continue

    if errors:
        print("Forbidden em-dash (U+2014) or en-dash (U+2013) found:")
        for err in errors:
            print(err)
        sys.exit(1)
    else:
        print("OK: No em-dashes or en-dashes found in tracked text files.")
        sys.exit(0)

if __name__ == "__main__":
    main()
