"""Tests for googleauth HTTP transport behavior."""

from app import googleauth
from app.googleauth import _default_fetch, TOKENINFO_URL


def test_default_fetch_posts_form_body(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        def json(self):
            return {"aud": "test-aud-id"}

    class DummyClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, data=None, **kwargs):
            calls.append(("post", url, data, kwargs))
            return DummyResponse()

        def get(self, url, **kwargs):
            calls.append(("get", url, kwargs))
            raise AssertionError("get() should not be called")

    monkeypatch.setattr(googleauth.httpx, "Client", DummyClient)

    status, body = _default_fetch("abc")

    assert status == 200
    assert body == {"aud": "test-aud-id"}
    assert len(calls) == 1
    method, url, data, kwargs = calls[0]
    assert method == "post"
    assert url == TOKENINFO_URL
    assert data == {"access_token": "abc"}
    assert kwargs.get("params") is None
    assert "?" not in url
