#!/usr/bin/env python3
"""check_no_dashes.py - Scan tracked text files for em-dash (U+2014) and en-dash (U+2013).

Binary files (a NUL byte in the first 8 KiB) are skipped silently; any other
file that cannot be read or decoded is reported as a warning so a problem is
never hidden by a quiet skip.
"""

import subprocess
import sys
from pathlib import Path

FORBIDDEN = {"\u2014": "em-dash (U+2014)", "\u2013": "en-dash (U+2013)"}
SNIFF_BYTES = 8192


def get_tracked_files():
    try:
        res = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
        return [f for f in res.stdout.splitlines() if f.strip()]
    except Exception as e:
        print(f"Error running git ls-files: {e}")
        sys.exit(1)


def is_binary(path):
    with open(path, "rb") as f:
        return b"\0" in f.read(SNIFF_BYTES)


def main():
    errors = []
    warnings = []

    for rel_path in get_tracked_files():
        path = Path(rel_path)
        if not path.is_file():
            continue
        try:
            if is_binary(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f, start=1):
                    for char, label in FORBIDDEN.items():
                        if char in line:
                            errors.append(f"{rel_path}:{line_idx} {label}")
        except UnicodeDecodeError as e:
            warnings.append(f"{rel_path}: skipped, not valid UTF-8 ({e.reason} at byte {e.start})")
        except OSError as e:
            warnings.append(f"{rel_path}: skipped, unreadable ({e.strerror})")

    for w in warnings:
        print(f"WARNING: {w}")

    if errors:
        print("Forbidden em-dash (U+2014) or en-dash (U+2013) found:")
        for err in errors:
            print(err)
        sys.exit(1)
    print(f"OK: No em-dashes or en-dashes found in tracked text files ({len(warnings)} skipped with warnings).")
    sys.exit(0)


if __name__ == "__main__":
    main()
