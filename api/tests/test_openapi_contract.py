"""Validate real wire responses and prove the complete contract gate detects drift."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from datetime import datetime

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import CollectionRequest
from authhelper import ADMIN_AUTH, agent_auth
from deviceauth import human_get, human_post, ensure_registered

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / ".github/gates/check_openapi_parity.py"
client = TestClient(app)


def _validate(data, schema):
    contract = app.openapi()
    root = {**schema, "components": contract["components"]}
    jsonschema.Draft202012Validator(root, format_checker=jsonschema.FormatChecker()).validate(data)


def _response_schema(path, method, status):
    return app.openapi()["paths"][path][method]["responses"][str(status)]["content"]["application/json"]["schema"]


def test_real_request_answer_read_and_idempotent_replay_match_contract(monkeypatch):
    monkeypatch.setenv("HCA_REQUIRE_GOOGLE_LINK", "0")
    headers = agent_auth("contract-agent")
    body = {"caller": "contract-agent", "audience": "owner", "title": "Choose a color", "control": "radio",
            "options_or_fields": [{"id": "red", "label": "Red"}, {"id": "blue", "label": "Blue"}],
            "suggested": {"option_id": "red"}, "deliver_to": {"mode": "poll"}}
    create = client.post("/v1/requests", json=body, headers={**headers, "Idempotency-Key": "contract-create"})
    assert create.status_code == 201, create.text
    _validate(create.json(), _response_schema("/v1/requests", "post", 201))
    rid = create.json()["request_id"]
    view = client.get(f"/v1/requests/{rid}", headers=headers)
    assert view.status_code == 200
    _validate(view.json(), _response_schema("/v1/requests/{request_id}", "get", 200))
    answer_body = {"control": "radio", "value": "blue", "surface": "browser_overlay", "answered_by": "untrusted"}
    answer_path = f"/v1/requests/{rid}/answer"
    answer = human_post(client, answer_path, json=answer_body, headers={"Idempotency-Key": "contract-answer"})
    assert answer.status_code == 201, answer.text
    _validate(answer.json(), _response_schema("/v1/requests/{request_id}/answer", "post", 201))
    assert answer.json()["answered_by"] != "untrusted"
    replay = human_post(client, answer_path, json=answer_body, headers={"Idempotency-Key": "contract-answer"})
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.content == answer.content
    _validate(replay.json(), _response_schema("/v1/requests/{request_id}/answer", "post", 201))
    _validate(client.get(f"/v1/requests/{rid}", headers=headers).json(),
              _response_schema("/v1/requests/{request_id}", "get", 200))
    for path in ("/v1/inbox", "/v1/audit"):
        response = human_get(client, path)
        assert response.status_code == 200
        _validate(response.json(), _response_schema(path, "get", 200))
    exported = client.get("/v1/tenants/default/records", headers=ADMIN_AUTH)
    assert exported.status_code == 200
    _validate(exported.json(), _response_schema("/v1/tenants/{tenant_id}/records", "get", 200))


def test_admin_and_webhook_wire_timestamps_match_rfc3339_contract():
    ensure_registered()
    caller = "contract-" + uuid.uuid4().hex[:8]
    key = client.post("/v1/api-keys", json={"caller": caller}, headers=ADMIN_AUTH)
    assert key.status_code == 201
    _validate(key.json(), _response_schema("/v1/api-keys", "post", 201))
    tenant = client.post("/v1/tenants", json={"slug": caller, "name": "Contract check"}, headers=ADMIN_AUTH)
    assert tenant.status_code == 201
    _validate(tenant.json(), _response_schema("/v1/tenants", "post", 201))
    for path in ("/v1/api-keys", "/v1/devices", "/v1/tenants"):
        response = client.get(path, headers=ADMIN_AUTH)
        assert response.status_code == 200
        _validate(response.json(), _response_schema(path, "get", 200))
    headers = {"Authorization": "Bearer " + key.json()["api_key"]}
    hook = client.post("/v1/webhooks", json={"url": "https://contract.example/hook"}, headers=headers)
    assert hook.status_code == 201, hook.text
    _validate(hook.json(), _response_schema("/v1/webhooks", "post", 201))
    _validate(client.get("/v1/webhooks", headers=headers).json(), _response_schema("/v1/webhooks", "get", 200))


def test_error_and_scheduler_responses_match_contract(monkeypatch):
    monkeypatch.setenv("SWEEP_TRIGGER_TOKEN", "contract-sweep")
    sweep = client.post("/v1/internal/expiry-sweep", headers={"Authorization": "Bearer contract-sweep"})
    assert sweep.status_code == 200, sweep.text
    _validate(sweep.json(), _response_schema("/v1/internal/expiry-sweep", "post", 200))
    denied = client.post("/v1/internal/expiry-sweep")
    assert denied.status_code == 401
    _validate(denied.json(), _response_schema("/v1/internal/expiry-sweep", "post", 401))
    import app.main as main
    monkeypatch.setattr(main, "db_ping", lambda: False)
    degraded = client.get("/health")
    assert degraded.status_code == 503
    _validate(degraded.json(), _response_schema("/health", "get", 503))


@pytest.mark.parametrize("drift", ["bound", "required", "security", "header", "body", "status"])
def test_gate_fails_for_schema_security_body_header_and_status_drift(tmp_path, drift):
    changed = deepcopy(app.openapi())
    operation = changed["paths"]["/v1/requests"]["post"]
    if drift == "bound":
        changed["components"]["schemas"]["Option"]["properties"]["label"]["maxLength"] = 99999
    elif drift == "required":
        changed["components"]["schemas"]["CollectionRequest"]["required"].remove("caller")
    elif drift == "security":
        operation["security"] = []
    elif drift == "header":
        operation["parameters"] = [p for p in operation["parameters"] if p["name"] != "Idempotency-Key"]
    elif drift == "body":
        operation["requestBody"]["content"] = {"text/plain": {"schema": {"type": "string"}}}
    else:
        operation["responses"].pop("409")
    source = tmp_path / "changed.yaml"
    source.write_text(yaml.safe_dump(changed))
    result = subprocess.run([sys.executable, str(GATE), "--yaml-path", str(source)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "OpenAPI contract difference" in result.stdout


def test_served_schema_and_export_are_identical_and_export_is_standalone(tmp_path):
    assert client.get("/openapi.json").json() == app.openapi()
    assert yaml.safe_load((ROOT / "api/openapi.yaml").read_text()) == app.openapi()
    env = {key: value for key, value in os.environ.items()
           if key not in {"DATABASE_URL", "HCA_TEST_DATABASE_URL", "HCA_ADMIN_TOKEN", "HCA_API_KEY_PEPPER", "PYTHONPATH"}}
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    for destination in (first, second):
        result = subprocess.run([sys.executable, str(GATE.parent / "export_openapi.py"),
                                 "--output-path", str(destination)], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    assert first.read_bytes() == second.read_bytes()
    assert yaml.safe_load(first.read_text()) == app.openapi()


def test_all_device_headers_are_required_together_and_sweep_uses_its_own_bearer():
    schema = app.openapi()
    for methods in schema["paths"].values():
        for operation in methods.values():
            for requirement in operation.get("security", []):
                if "DeviceSignature" in requirement:
                    assert set(requirement) == {"DeviceID", "DeviceTimestamp", "DeviceNonce", "DeviceSignature"}
                    parameters = {p["name"]: p for p in operation["parameters"] if p["in"] == "header"}
                    for header in ("X-HCA-Device", "X-HCA-Timestamp", "X-HCA-Nonce", "X-HCA-Signature"):
                        assert parameters[header]["required"] is True
    assert schema["paths"]["/v1/internal/expiry-sweep"]["post"]["security"] == [{"SweepBearer": []}]


@pytest.mark.parametrize("contents", [{"allowed_differences": ["anything"]}, []])
def test_gate_refuses_exceptions(tmp_path, contents):
    allowlist = tmp_path / "exceptions.json"
    allowlist.write_text(json.dumps(contents))
    result = subprocess.run([sys.executable, str(GATE), "--allowlist-path", str(allowlist)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and "no allowlisted differences" in result.stderr


def test_comparison_ignores_object_key_order():
    spec = importlib.util.spec_from_file_location("contract_gate", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    assert gate.schema_differences({"a": 1, "b": {"x": 2, "y": 3}}, {"b": {"y": 3, "x": 2}, "a": 1}) == []


def test_response_datetime_normalization_preserves_original_create_fingerprint():
    body = CollectionRequest(caller="agent", audience="owner", title="Confirm", control="chat",
                             options_or_fields=[], suggested={}, deliver_to={"mode": "poll"},
                             deadline_at=datetime(2026, 10, 13))
    assert body.model_dump(mode="json", context={"preserve_datetime_input": True})["deadline_at"] == "2026-10-13T00:00:00"
    assert body.model_dump(mode="json")["deadline_at"] == "2026-10-13T00:00:00Z"
    assert body.deadline_at.tzinfo is None


@pytest.mark.parametrize("boundary", ["depth", "nodes"])
def test_maximum_client_metadata_can_be_answered_read_and_replayed(monkeypatch, boundary):
    monkeypatch.setenv("HCA_REQUIRE_GOOGLE_LINK", "0")
    if boundary == "depth":
        metadata = "leaf"
        for _ in range(12):
            metadata = {"child": metadata}
        excessive = {"child": metadata}
    else:
        # One root dictionary, one array and 9998 values = 10000 nodes.
        metadata = {"items": [0] * 9998}
        excessive = {"items": [0] * 9999}
    caller = "boundary-" + boundary
    headers = agent_auth(caller)
    create = client.post("/v1/requests", headers=headers, json={
        "caller": caller, "audience": "owner", "title": "Boundary decision", "control": "chat",
        "options_or_fields": [], "suggested": {}, "deliver_to": {"mode": "poll"}, "zoom": metadata,
    })
    assert create.status_code == 201, create.text
    rid = create.json()["request_id"]
    path = f"/v1/requests/{rid}/answer"
    body = {"control": "chat", "value": "ok", "surface": "browser_overlay", "passthrough": metadata}
    rejected = human_post(client, path, json={**body, "passthrough": excessive})
    assert rejected.status_code == 422
    answer = human_post(client, path, json=body, headers={"Idempotency-Key": "maximum-" + boundary})
    assert answer.status_code == 201, answer.text
    _validate(answer.json(), _response_schema("/v1/requests/{request_id}/answer", "post", 201))
    assert answer.json()["passthrough"]["client_metadata"]["passthrough"] == metadata
    replay = human_post(client, path, json=body, headers={"Idempotency-Key": "maximum-" + boundary})
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.content == answer.content
    _validate(replay.json(), _response_schema("/v1/requests/{request_id}/answer", "post", 201))
    view = client.get(f"/v1/requests/{rid}", headers=headers)
    assert view.status_code == 200
    _validate(view.json(), _response_schema("/v1/requests/{request_id}", "get", 200))
    assert view.json()["answer"]["passthrough"] == answer.json()["passthrough"]
    assert view.json()["request"]["zoom"] == metadata
