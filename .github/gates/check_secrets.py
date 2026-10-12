#!/usr/bin/env python3
"""Scan source and every fetched Git ref, with exact reviewed exceptions.

Findings are fully redacted. An exception identifies one finding and the
SHA-256 of its entire file, so new material at an old location is refused.
Default scanner rules and HCA credential patterns remain enabled.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
GATES = Path(__file__).resolve().parent


def git(root, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=root, stderr=subprocess.PIPE)


def file_digest(root, finding):
    name = finding["File"]
    try:
        data = (git(root, "show", finding["Commit"] + ":" + name)
                if finding.get("Commit") else (root / name).read_bytes())
    except (OSError, subprocess.CalledProcessError):
        return None
    return hashlib.sha256(data).hexdigest()


def accepted(root, finding, exceptions):
    return any(item["fingerprint"] == finding["Fingerprint"]
               and item["file_sha256"] == file_digest(root, finding)
               and bool(item.get("reason")) for item in exceptions)


def run_scan(binary, root, target, mode, temporary, exceptions):
    report = temporary / (mode + ".json")
    command = [binary, mode, str(target), "--config", str(GATES / "gitleaks.toml"),
               "--gitleaks-ignore-path", str(temporary / "empty-ignore"),
               "--ignore-gitleaks-allow", "--redact=100", "--no-banner", "--no-color",
               "--report-format=json", "--report-path", str(report), "--timeout=180"]
    if mode == "git":
        command.append("--log-opts=--all")
    result = subprocess.run(command, capture_output=True, timeout=200)
    if result.returncode not in (0, 1) or not report.is_file():
        raise ValueError(f"{mode} credential scan could not complete")
    findings = json.loads(report.read_text())
    if mode == "dir":
        for finding in findings:
            name = Path(finding["File"]).resolve().relative_to(target.resolve()).as_posix()
            finding["File"] = name
            finding["Fingerprint"] = f"{name}:{finding['RuleID']}:{finding['StartLine']}"
    rejected = [finding for finding in findings if not accepted(root, finding, exceptions)]
    for finding in rejected:
        path_id = hashlib.sha256(finding["File"].encode()).hexdigest()[:16]
        print(f"FAIL: {mode}: file-id={path_id}, line={finding['StartLine']}, rule={finding['RuleID']} (redacted)")
    print(f"{mode}: {len(findings)} findings, {len(findings) - len(rejected)} exact reviewed exceptions")
    return not rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--scanner", default="gitleaks")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        version = subprocess.check_output([args.scanner, "version"], text=True).strip()
        if version != "8.30.1":
            raise ValueError("Use the reviewed Gitleaks 8.30.1 scanner release")
        if git(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
            raise ValueError("History scan requires a complete clone; fetch-depth must be zero")
        exceptions = json.loads((GATES / "secret_scan_exceptions.json").read_text())
        with tempfile.TemporaryDirectory(prefix="hca-secrets-") as directory:
            temporary = Path(directory)
            (temporary / "empty-ignore").write_text("")
            snapshot = temporary / "source"
            snapshot.mkdir()
            names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
            for name in filter(None, names):
                source = root / name
                if not source.exists():
                    continue
                if source.is_symlink() or not source.is_file():
                    raise ValueError("Tracked source must contain regular files")
                destination = snapshot / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            source_ok = run_scan(args.scanner, root, snapshot, "dir", temporary, exceptions)
            history_ok = run_scan(args.scanner, root, root, "git", temporary, exceptions)
        if source_ok and history_ok:
            print("OK: full source and fetched history credential scans completed")
            return 0
        return 1
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as exc:
        print(f"FAIL: credential scan did not complete: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
