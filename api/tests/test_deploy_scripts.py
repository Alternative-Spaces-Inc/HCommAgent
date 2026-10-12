"""Unit tests for the deployment pipeline helpers in scripts/ (review item 32):
check_config.py must fail closed, candidate_revision.py must find exactly the
tagged revision."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check_config = _load("check_config")
candidate_revision = _load("candidate_revision")


def _secret(name):
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": name.lower(), "key": "latest"}}}


def good_revision(commit="abc123"):
    env = [_secret(n) for n in sorted(check_config.REQUIRED_SECRETS)]
    env += [
        {"name": "HCA_GOOGLE_CLIENT_IDS", "value": "id-a,id-b"},
        {"name": "HCA_OWNER_EMAILS", "value": "owner@example.com"},
        {"name": "HCA_REQUIRE_GOOGLE_LINK", "value": "1"},
        {"name": "HCA_DB_POOL_SIZE", "value": "5"},
    ]
    return {"metadata": {"labels": {"commit": commit}}, "spec": {"containers": [{"env": env}]}}


def test_good_revision_passes():
    assert check_config.check(good_revision(), "abc123") == []


def test_service_shape_is_accepted():
    rev = good_revision()
    svc = {"spec": {"template": {"metadata": {"labels": {"commit": "abc123"}}, "spec": rev["spec"]}}}
    assert check_config.check(svc, "abc123") == []


def test_no_container_fails():
    assert check_config.check({"spec": {"containers": []}}, None) == ["no container in the description"]


@pytest.mark.parametrize("missing", sorted(check_config.REQUIRED_SECRETS))
def test_missing_secret_fails(missing):
    rev = good_revision()
    rev["spec"]["containers"][0]["env"] = [e for e in rev["spec"]["containers"][0]["env"] if e["name"] != missing]
    assert any(missing in p and "secret" in p for p in check_config.check(rev, None))


def test_secret_given_as_plain_value_fails():
    rev = good_revision()
    for e in rev["spec"]["containers"][0]["env"]:
        if e["name"] == "HCA_ADMIN_TOKEN":
            e.pop("valueFrom")
            e["value"] = "plain"
    assert any("HCA_ADMIN_TOKEN" in p for p in check_config.check(rev, None))


def test_empty_owner_list_fails():
    rev = good_revision()
    for e in rev["spec"]["containers"][0]["env"]:
        if e["name"] == "HCA_OWNER_EMAILS":
            e["value"] = "  "
    assert "required env var empty: HCA_OWNER_EMAILS" in check_config.check(rev, None)


def test_google_link_gate_off_fails():
    rev = good_revision()
    for e in rev["spec"]["containers"][0]["env"]:
        if e["name"] == "HCA_REQUIRE_GOOGLE_LINK":
            e["value"] = "0"
    assert "HCA_REQUIRE_GOOGLE_LINK must be 1 on a deployed revision" in check_config.check(rev, None)


def test_forbidden_switch_fails():
    rev = good_revision()
    rev["spec"]["containers"][0]["env"].append({"name": "HCA_WEBHOOK_ALLOW_INSECURE", "value": "1"})
    assert "forbidden local only switch present: HCA_WEBHOOK_ALLOW_INSECURE" in check_config.check(rev, None)


def test_dev_named_variable_fails():
    rev = good_revision()
    rev["spec"]["containers"][0]["env"].append({"name": "HCA_LOCAL_AUTH_BYPASS", "value": "1"})
    assert any("development switch" in p for p in check_config.check(rev, None))


def test_commit_label_mismatch_fails():
    assert any("commit label" in p for p in check_config.check(good_revision("old"), "new"))


def test_cli_exit_codes(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(good_revision()))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"spec": {"containers": []}}))
    assert subprocess.run([sys.executable, str(SCRIPTS / "check_config.py"), str(good)], capture_output=True).returncode == 0
    assert subprocess.run([sys.executable, str(SCRIPTS / "check_config.py"), str(bad)], capture_output=True).returncode == 1
    assert subprocess.run([sys.executable, str(SCRIPTS / "check_config.py")], input=b"not json", capture_output=True).returncode == 1


def _service(traffic):
    return {"status": {"traffic": traffic}}


def test_candidate_found():
    svc = _service([
        {"revisionName": "svc-00080-abc", "percent": 100},
        {"revisionName": "svc-00081-def", "percent": 0, "tag": "candidate", "url": "https://candidate---svc.example.run.app/"},
    ])
    target = candidate_revision.find_candidate(svc)
    assert target["revisionName"] == "svc-00081-def"
    assert target["url"].rstrip("/") == "https://candidate---svc.example.run.app"


def test_candidate_missing_or_duplicated_fails():
    with pytest.raises(SystemExit):
        candidate_revision.find_candidate(_service([{"revisionName": "a", "percent": 100}]))
    with pytest.raises(SystemExit):
        candidate_revision.find_candidate(_service([
            {"revisionName": "a", "tag": "candidate", "url": "u"},
            {"revisionName": "b", "tag": "candidate", "url": "v"},
        ]))


def test_candidate_cli(tmp_path):
    svc = json.dumps(_service([{"revisionName": "svc-1", "percent": 0, "tag": "candidate", "url": "https://x.example/"}]))
    out = subprocess.run([sys.executable, str(SCRIPTS / "candidate_revision.py"), "--name"], input=svc.encode(), capture_output=True)
    assert out.returncode == 0 and out.stdout.strip() == b"svc-1"
    out = subprocess.run([sys.executable, str(SCRIPTS / "candidate_revision.py"), "--url"], input=svc.encode(), capture_output=True)
    assert out.returncode == 0 and out.stdout.strip() == b"https://x.example"
