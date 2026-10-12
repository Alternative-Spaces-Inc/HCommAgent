#!/usr/bin/env python3
"""Credential scan with masked output and optional full-history scanning.

The optional client-data denylist supplements real credential checks.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

PATTERNS = {
    "GitHub credential": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{70,})"),
    "Google access token": re.compile(r"ya29\.[A-Za-z0-9_-]{40,}"),
    "AWS access key": re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "HCA agent credential": re.compile(r"hca_live_[a-f0-9]{12}_[A-Za-z0-9_-]{32,}"),
    "Webhook credential": re.compile(r"whsec_[A-Za-z0-9_-]{43,}"),
    "Private key": re.compile(r"-----BEGIN (?:EC |RSA |OPENSSH )?PRIVATE KEY-----.*?-----END (?:EC |RSA |OPENSSH )?PRIVATE KEY-----\n?", re.S),
}
ROOT = Path(__file__).resolve().parents[2]
PUBLIC_KEYS = set(json.loads(Path(__file__).with_name("public_test_key_hashes.json").read_text()))


def findings(text):
    text = re.sub(r"(?m)^\+", "", text).replace("\\n", "\n")
    found = []
    for label, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            digest = hashlib.sha256(match.group().encode()).hexdigest()
            if label == "Private key" and digest in PUBLIC_KEYS:
                continue  # Exact public signature test key, never production material.
            found.append(label + " (fingerprint " + digest[:10] + ")")
    return sorted(set(found))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, text=True).splitlines()
    denylist = [line.strip() for line in Path(__file__).with_name("client_data_denylist.txt").read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    failed = False
    scanned = 0
    for name in paths:
        path = ROOT / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="replace")
        issues = findings(text)
        if any(term.casefold() in text.casefold() for term in denylist):
            issues.append("client data denylist match")
        if issues:
            print(name + ": " + "; ".join(issues))
            failed = True
        scanned += 1
    if args.history:
        patches = subprocess.check_output(["git", "log", "--all", "--format=", "--patch"], cwd=ROOT, text=True, errors="replace")
        issues = findings(patches)
        if issues:
            print("Git history: " + "; ".join(issues))
            failed = True
    if not failed:
        print(f"OK: credential patterns checked in {scanned} text files" + (" and Git history" if args.history else ""))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
