"""Tests for QueryCredentialGuard middleware."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.limits import RateLimitBucket
from app.main import app
from app.middleware import QueryCredentialGuard
from app.models import Ask, AuditRow


FORBIDDEN_NAMES = ["access_token", "api_key", "apikey", "token", "key", "secret"]


def _variations(name: str) -> list[str]:
    return [
        name.lower(),
        name.upper(),
        name.capitalize(),
    ]


@pytest.fixture
def test_client():
    return TestClient(app)


def test_forbidden_query_params_rejected_on_healthz_and_requests(test_client):
    with SessionLocal() as db:
        initial_ask_count = db.query(Ask).count()
        initial_audit_count = db.query(AuditRow).count()
        initial_rate_count = db.query(RateLimitBucket).count()

    for name in FORBIDDEN_NAMES:
        for var_name in _variations(name):
            # GET /healthz
            res_get = test_client.get(f"/healthz?{var_name}=secret123")
            assert res_get.status_code == 400
            assert res_get.json() == {"detail": "credentials are not accepted in the query string"}

            # POST /v1/requests
            res_post = test_client.post(f"/v1/requests?{var_name}=secret123", json={})
            assert res_post.status_code == 400
            assert res_post.json() == {"detail": "credentials are not accepted in the query string"}

    # Prove no handler ran / no DB mutations occurred
    with SessionLocal() as db:
        assert db.query(Ask).count() == initial_ask_count
        assert db.query(AuditRow).count() == initial_audit_count
        assert db.query(RateLimitBucket).count() == initial_rate_count


def test_handler_counter_proves_no_execution():
    call_count = [0]
    mini_app = FastAPI()

    @mini_app.get("/counter")
    def counter():
        call_count[0] += 1
        return {"ok": True}

    mini_app.add_middleware(QueryCredentialGuard)
    client = TestClient(mini_app)

    res = client.get("/counter?api_key=12345")
    assert res.status_code == 400
    assert res.json() == {"detail": "credentials are not accepted in the query string"}
    assert call_count[0] == 0


def test_allowed_query_parameters_pass(test_client):
    res_get = test_client.get("/healthz?safe_param=hello&page=1")
    assert res_get.status_code == 200
    assert res_get.json().get("status") == "ok"


def test_bare_token_without_equals_refused(test_client):
    res_get = test_client.get("/healthz?token")
    assert res_get.status_code == 400
    assert res_get.json() == {"detail": "credentials are not accepted in the query string"}

    res_post = test_client.post("/v1/requests?token", json={})
    assert res_post.status_code == 400
    assert res_post.json() == {"detail": "credentials are not accepted in the query string"}
