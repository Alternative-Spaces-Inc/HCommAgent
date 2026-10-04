"""HCA-P7: Google sign-in linked to a device (app/googleauth.py,
POST /v1/devices/self/google-link, HCA_REQUIRE_GOOGLE_LINK)."""

import os

import pytest
from fastapi.testclient import TestClient

from app import googleauth, models
from app.db import SessionLocal
from app.main import app
from tests import authhelper
from tests.deviceauth import DEVICE_ID, ensure_registered, human_get, human_post

CLIENT = "123-test.apps.googleusercontent.com"


def _info(**over):
    base = {"aud": CLIENT, "azp": CLIENT, "sub": "sub-1", "email": "Owner@Example.com",
            "email_verified": "true", "expires_in": "3000"}
    base.update(over)
    return base


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def google_env(monkeypatch):
    monkeypatch.setenv("HCA_GOOGLE_CLIENT_ID", CLIENT)
    monkeypatch.delenv("HCA_OWNER_EMAILS", raising=False)
    monkeypatch.delenv("HCA_REQUIRE_GOOGLE_LINK", raising=False)
    yield
    googleauth.set_fetcher(None)
    with SessionLocal() as db:
        d = db.get(models.Device, DEVICE_ID)
        if d is not None:
            d.google_sub = d.google_email = d.google_linked_at = None
            db.commit()


def fake(status=200, info=None, seen=None):
    def fetch(token):
        if seen is not None:
            seen.append(token)
        return status, (info if info is not None else _info())
    googleauth.set_fetcher(fetch)


def test_link_records_identity_and_self_view_shows_it(client):
    seen = []
    fake(seen=seen)
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "ya29.test"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["device_id"] == DEVICE_ID
    assert body["google_email"] == "owner@example.com"
    assert body["google_linked_at"]
    assert seen == ["ya29.test"]
    r = human_get(client, "/v1/devices/self")
    assert r.status_code == 200
    assert r.json()["google_email"] == "owner@example.com"
    with SessionLocal() as db:
        assert db.get(models.Device, DEVICE_ID).google_sub == "sub-1"


def test_link_requires_device_signature(client):
    fake()
    r = client.post("/v1/devices/self/google-link", json={"access_token": "ya29.test"})
    assert r.status_code == 401


def test_token_for_another_client_is_refused(client):
    fake(info=_info(aud="other-client", azp="other-client"))
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "t"})
    assert r.status_code == 403
    assert "another client" in r.json()["detail"]


def test_unverified_email_is_refused(client):
    fake(info=_info(email_verified="false"))
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "t"})
    assert r.status_code == 403


def test_expired_or_invalid_token_is_401(client):
    fake(status=400, info={"error": "invalid_token"})
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "t"})
    assert r.status_code == 401
    fake(info=_info(expires_in="0"))
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "t"})
    assert r.status_code == 401


def test_owner_list_gates_the_account(client, monkeypatch):
    monkeypatch.setenv("HCA_OWNER_EMAILS", "someone@else.test, Owner@Example.com")
    fake()
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "t"})
    assert r.status_code == 200
    monkeypatch.setenv("HCA_OWNER_EMAILS", "someone@else.test")
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "t"})
    assert r.status_code == 403
    assert "not an owner" in r.json()["detail"]


def test_missing_client_id_fails_closed(client, monkeypatch):
    monkeypatch.delenv("HCA_GOOGLE_CLIENT_ID")
    fake()
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "t"})
    assert r.status_code == 503


def test_require_flag_blocks_unlinked_device_until_it_links(client, monkeypatch):
    ensure_registered()
    monkeypatch.setenv("HCA_REQUIRE_GOOGLE_LINK", "1")
    r = human_get(client, "/v1/inbox")
    assert r.status_code == 403
    assert "not linked" in r.json()["detail"]
    r = human_get(client, "/v1/audit")
    assert r.status_code == 403
    # the link route itself stays open to a signed but unlinked device
    fake()
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "t"})
    assert r.status_code == 200
    r = human_get(client, "/v1/inbox")
    assert r.status_code == 200
    r = human_get(client, "/v1/audit")
    assert r.status_code == 200


def test_flag_off_leaves_unlinked_device_working(client):
    ensure_registered()
    r = human_get(client, "/v1/inbox")
    assert r.status_code == 200


def test_second_client_id_from_list_is_accepted(client, monkeypatch):
    """HCA-P13: the macOS app's Desktop client is a second allowed audience."""
    monkeypatch.setenv("HCA_GOOGLE_CLIENT_IDS", " desktop-client.apps.googleusercontent.com ,other.apps.googleusercontent.com")
    fake(info=_info(aud="desktop-client.apps.googleusercontent.com", azp="desktop-client.apps.googleusercontent.com"))
    r = human_post(client, "/v1/devices/self/google-link", {"access_token": "ya29.desktop"})
    assert r.status_code == 200, r.text
    assert r.json()["google_email"] == "owner@example.com"


def test_list_only_configuration_works_and_unknown_client_still_refused(client, monkeypatch):
    monkeypatch.delenv("HCA_GOOGLE_CLIENT_ID")
    monkeypatch.setenv("HCA_GOOGLE_CLIENT_IDS", "desktop-client.apps.googleusercontent.com")
    fake(info=_info(aud="desktop-client.apps.googleusercontent.com", azp="desktop-client.apps.googleusercontent.com"))
    assert human_post(client, "/v1/devices/self/google-link", {"access_token": "ya29.a"}).status_code == 200
    fake(info=_info(aud="stranger.apps.googleusercontent.com", azp="stranger.apps.googleusercontent.com"))
    assert human_post(client, "/v1/devices/self/google-link", {"access_token": "ya29.b"}).status_code == 403
