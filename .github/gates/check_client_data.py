#!/usr/bin/env python3
"""check_client_data.py - Scan tracked files against the denylist with masked output.

Matching is a case-insensitive substring match on purpose: a client name
inside a longer token must still fail the gate. Output is capped so a large
leak does not flood the log; the count of omitted lines is printed.
"""

import re
import subprocess
import sys
from pathlib import Path

MAX_REPORTED = 50
SNIFF_BYTES = 8192


# Paths whose content is deliberately published test material (shared test
# vectors with a throwaway key). Nothing here may hold a live credential.
ALLOWED_PATH_PREFIXES = (
    "api/tests/vectors/",
    ".github/gates/client_data_denylist.txt",
    ".github/gates/check_client_data.py",
)


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


def is_binary(path):
    with open(path, "rb") as f:
        return b"\0" in f.read(SNIFF_BYTES)


def mask_term(term):
    """Show only the first and last character; short terms are fully masked."""
    if len(term) <= 3:
        return "*" * len(term)
    return term[0] + "*" * (len(term) - 2) + term[-1]


def main():
    terms = load_denylist()
    if not terms:
        print("OK: client data denylist is empty.")
        sys.exit(0)

    patterns = []
    for term in terms:
        if term.startswith("re:"):
            # Regular expression line (see client_data_denylist.txt); case sensitive
            # because secret shapes depend on their exact prefix.
            patterns.append((term, re.compile(term[3:])))
        else:
            patterns.append((term, re.compile(re.escape(term), re.IGNORECASE)))
    errors = []
    warnings = []

    for rel_path in get_tracked_files():
        path = Path(rel_path)
        if not path.is_file():
            continue
        if any(rel_path.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES):
            continue
        try:
            if is_binary(path):
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line_idx, line in enumerate(f, start=1):
                    for term, pat in patterns:
                        if pat.search(line):
                            errors.append(f"{rel_path}:{line_idx} (matched masked pattern: {mask_term(term)})")
                            break
        except OSError as e:
            warnings.append(f"{rel_path}: skipped, unreadable ({e.strerror})")

    for w in warnings:
        print(f"WARNING: {w}")

    if errors:
        print("Client data denylist matches found:")
        for err in errors[:MAX_REPORTED]:
            print(err)
        if len(errors) > MAX_REPORTED:
            print(f"... {len(errors) - MAX_REPORTED} more matches omitted")
        sys.exit(1)
    print("OK: No client data matches found.")
    sys.exit(0)


if __name__ == "__main__":
    main()
