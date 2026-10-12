"""Real scanner failure/redaction proofs against disposable local Git history.

API jobs may skip these tests when the reviewed scanner is unavailable. The
Credential and History Scan job installs it and sets HCA_GITLEAKS_BINARY,
which makes a missing or wrong configured binary a failure.
"""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GATES = Path(__file__).resolve().parents[2] / ".github" / "gates"
EXPECTED_SCANNER_VERSION = "8.30.1"


@pytest.fixture(scope="module")
def scanner():
    explicit = os.environ.get("HCA_GITLEAKS_BINARY")
    binary = explicit or shutil.which("gitleaks")
    if not binary:
        pytest.skip("Reviewed Gitleaks 8.30.1 unavailable; the credential-history job installs it explicitly")
    try:
        result = subprocess.run([binary, "version"], text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        if explicit:
            pytest.fail("Configured credential scanner is unavailable", pytrace=False)
        pytest.skip("Local credential scanner is unavailable; the credential-history job provides the reviewed binary")
    if result.returncode or result.stdout.strip() != EXPECTED_SCANNER_VERSION:
        if explicit:
            pytest.fail("Configured credential scanner is not the reviewed version", pytrace=False)
        pytest.skip("Local scanner is not Gitleaks 8.30.1; credential-history CI installs the exact version")
    return binary


def git(repo, *arguments):
    env = os.environ.copy()
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "Synthetic test", "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
                "GIT_COMMITTER_NAME": "Synthetic test", "GIT_COMMITTER_EMAIL": "synthetic@example.invalid"})
    result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull, *arguments],
                            cwd=repo, env=env, text=True, capture_output=True, timeout=20)
    if result.returncode:
        # Git error output can include filenames containing synthetic secrets.
        pytest.fail("Disposable Git fixture setup failed", pytrace=False)
    return result.stdout


@pytest.fixture
def harness(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    (repo / "README.txt").write_text("Synthetic scanner fixture, no credentials.\n")
    git(repo, "add", "README.txt")
    git(repo, "commit", "-m", "Create synthetic fixture")
    gates = tmp_path / "fixture-gates"
    gates.mkdir()
    for name in ("check_secrets.py", "gitleaks.toml"):
        shutil.copyfile(GATES / name, gates / name)
    (gates / "secret_scan_exceptions.json").write_text("[]\n")
    return repo, gates


def credential():
    # Runtime generation avoids adding a scanner exception for this test.
    return "_".join(("hca", "live", secrets.token_hex(6), secrets.token_urlsafe(24)))


def gate(repo, gates, scanner, hidden=()):
    result = subprocess.run([sys.executable, str(gates / "check_secrets.py"), "--root", str(repo), "--scanner", scanner],
                            text=True, capture_output=True, timeout=45)
    output = result.stdout + result.stderr
    if any(value in output for value in hidden):
        pytest.fail("Credential gate printed sensitive fixture material", pytrace=False)
    return result.returncode, output


def test_new_and_deleted_history_credentials_fail_without_exposing_values(harness, scanner):
    repo, gates = harness
    assert gate(repo, gates, scanner)[0] == 0
    token = credential()
    # The sensitive filename proves metadata output is redacted too. Inline
    # allow comments and an ignore file must not bypass the wrapper policy.
    path = repo / (token + ".txt")
    path.write_text('token = "' + token + '" # gitleaks:allow\n')
    (repo / ".gitleaksignore").write_text("*\n")
    status, output = gate(repo, gates, scanner, hidden=(token,))
    assert status == 1 and "FAIL: dir:" in output
    git(repo, "add", "--all")
    git(repo, "commit", "-m", "Add synthetic credential")
    path.unlink()
    git(repo, "add", "--all")
    git(repo, "commit", "-m", "Delete synthetic credential")
    status, output = gate(repo, gates, scanner, hidden=(token,))
    assert status == 1 and "FAIL: git:" in output
    assert "dir: 0 findings" in output


def findings_for(path, gates, scanner, tmp_path):
    report = tmp_path / "synthetic-findings.json"
    ignore = tmp_path / "empty-ignore"
    ignore.write_text("")
    result = subprocess.run([scanner, "dir", str(path.parent), "--config", str(gates / "gitleaks.toml"),
        "--gitleaks-ignore-path", str(ignore), "--ignore-gitleaks-allow", "--redact=100", "--no-banner", "--no-color",
        "--report-format=json", "--report-path", str(report)], capture_output=True, timeout=30)
    if result.returncode != 1 or not report.is_file():
        pytest.fail("Real scanner did not detect the synthetic credential", pytrace=False)
    return json.loads(report.read_text())


def test_exact_exception_rejects_changed_file_and_reused_location(harness, scanner, tmp_path):
    repo, gates = harness
    token = credential()
    path = repo / "fixture-credential.txt"
    original = 'token = "' + token + '"\n'
    path.write_text(original)
    findings = findings_for(path, gates, scanner, tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    exceptions = []
    for finding in findings:
        name = Path(finding["File"]).resolve().relative_to(repo.resolve()).as_posix()
        exceptions.append({"fingerprint": f"{name}:{finding['RuleID']}:{finding['StartLine']}",
                           "file_sha256": digest, "reason": "Generated synthetic fixture, exact reviewed bytes only"})
    (gates / "secret_scan_exceptions.json").write_text(json.dumps(exceptions))
    assert gate(repo, gates, scanner, hidden=(token,))[0] == 0
    path.write_text(original + "# Changed file bytes, same credential location\n")
    assert gate(repo, gates, scanner, hidden=(token,))[0] == 1
    replacement = credential()
    path.write_text('token = "' + replacement + '"\n')
    assert gate(repo, gates, scanner, hidden=(token, replacement))[0] == 1
    path.write_text(original)
    copied = repo / "different-location.txt"
    copied.write_text(original)
    assert gate(repo, gates, scanner, hidden=(token, replacement))[0] == 1


def test_shallow_history_is_refused(harness, scanner, tmp_path):
    repo, gates = harness
    shallow = tmp_path / "shallow"
    git(repo, "clone", "--depth", "1", "--no-local", repo.as_uri(), str(shallow))
    assert git(shallow, "rev-parse", "--is-shallow-repository").strip() == "true"
    status, output = gate(shallow, gates, scanner)
    assert status == 1 and "credential scan did not complete" in output
    assert "OK: full source" not in output


def test_lock_input_extra_change_requires_regeneration(tmp_path):
    """A same-version extra adds dependency obligations and invalidates locks."""
    root = tmp_path / "lock-fixture"
    copied_gates = root / ".github" / "gates"
    copied_gates.mkdir(parents=True)
    api = root / "api"
    api.mkdir()
    for name in ("check_dependency_locks.py", "dependency_inputs.json"):
        shutil.copyfile(GATES / name, copied_gates / name)
    source_api = GATES.parents[1] / "api"
    for name in ("requirements.txt", "requirements.lock", "requirements-dev.in", "requirements-dev.lock"):
        shutil.copyfile(source_api / name, api / name)
    command = [sys.executable, str(copied_gates / "check_dependency_locks.py")]
    clean = subprocess.run(command, text=True, capture_output=True, timeout=20)
    assert clean.returncode == 0
    path = api / "requirements.txt"
    before = path.read_text()
    assert "fastapi==" in before
    path.write_text(before.replace("fastapi==", "fastapi[all]==", 1))
    drifted = subprocess.run(command, text=True, capture_output=True, timeout=20)
    assert drifted.returncode == 1
    assert "lock input changed" in drifted.stdout + drifted.stderr
