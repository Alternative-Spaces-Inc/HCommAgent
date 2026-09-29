#!/usr/bin/env python3
"""check_client_data.py - Scan tracked files against denylist with masked output."""

import re
import subprocess
import sys
from pathlib import Path

def load_denylist():
    denylist_file = Path(__file__).parent / "client_data_denylist.txt"
    if not denylist_file.is_file():
        return []
    terms = []
    with open(denylist_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return terms

def get_tracked_files():
    try:
        res = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
        return [f for f in res.stdout.splitlines() if f.strip()]
    except Exception as e:
        print(f"Error running git ls-files: {e}")
        sys.exit(1)

def mask_term(term):
    if len(term) <= 2:
        return "*" * len(term)
    return term[0] + "*" * (len(term) - 2) + term[-1]

def main():
    terms = load_denylist()
    if not terms:
        print("OK: client data denylist is empty.")
        sys.exit(0)

    patterns = [(term, re.compile(re.escape(term), re.IGNORECASE)) for term in terms]
    files = get_tracked_files()
    errors = []

    for rel_path in files:
        path = Path(rel_path)
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line_idx, line in enumerate(f, start=1):
                    for term, pat in patterns:
                        if pat.search(line):
                            masked = mask_term(term)
                            errors.append(f"{rel_path}:{line_idx} (matched masked pattern: {masked})")
        except Exception:
            continue

    if errors:
        print("Client data denylist matches found:")
        for err in errors:
            print(err)
        sys.exit(1)
    else:
        print("OK: No client data matches found.")
        sys.exit(0)

if __name__ == "__main__":
    main()
