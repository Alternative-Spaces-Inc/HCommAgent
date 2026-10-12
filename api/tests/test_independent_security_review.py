"""Independent regressions for reviewed boundaries; synthetic data and isolated test DB."""
import asyncio

import pytest

from app import inbox_api, models
from app.config import validate_environment
from app.middleware import RequestBounds


def test_production_rejects_an_empty_encryption_keyring():
    config = {"HCA_ENV": "production", "DATABASE_URL": "postgresql://synthetic",
              "HCA_ADMIN_TOKEN": "a" * 32, "HCA_API_KEY_PEPPER": "p" * 32,
              "SWEEP_TRIGGER_TOKEN": "s" * 32, "HCA_REQUIRE_GOOGLE_LINK": "1",
              "HCA_GOOGLE_CLIENT_ID": "client", "HCA_WEBHOOK_ENCRYPTION_KEYS": "{}",
              "HCA_WEBHOOK_CURRENT_KEY_ID": "missing"}
    with pytest.raises(RuntimeError, match="Invalid production configuration"):
        validate_environment(config)


def test_replay_checks_revocation_between_delayed_deliveries(monkeypatch):
    replay = [{"id": n, "event": "request.answered", "data": {"request_id": f"synthetic-{n}"}}
              for n in (1, 2)]
    monkeypatch.setattr(inbox_api, "_replay_for", lambda *args: replay)

    async def scenario():
        allowed = [True]
        checks = []
        def authorized(_):
            checks.append(allowed[0])
            return allowed[0]
        source = inbox_api.inbox_event_source(models.Device(id="review-replay", tenant_id="default"),
            keepalive_seconds=.02, authorized_fn=authorized, last_event_id=0)
        try:
            assert await anext(source) == ": connected\n\n"
            assert "synthetic-1" in await anext(source)
            allowed[0] = False
            await asyncio.sleep(.04)
            chunk = await anext(source)
            assert "stream.closed" in chunk and "synthetic-2" not in chunk
            assert len(checks) >= 2
        finally:
            await source.aclose()
    asyncio.run(scenario())


def test_bounded_body_does_not_retain_unbounded_chunk_envelopes():
    async def scenario():
        incoming = iter([{"type": "http.request", "body": b"", "more_body": True}] * 1000 +
                        [{"type": "http.request", "body": b"x", "more_body": False}])
        delivered = []
        async def receive():
            return next(incoming)
        async def app(scope, bounded_receive, send):
            while True:
                message = await bounded_receive()
                delivered.append(message)
                if not message.get("more_body", False):
                    break
        async def send(message):
            pass
        await RequestBounds(app)({"type": "http", "method": "POST"}, receive, send)
        assert b"".join(message["body"] for message in delivered) == b"x"
        assert len(delivered) <= 2, "chunk envelopes must be coalesced within the byte budget"
    asyncio.run(scenario())


def test_nonfinite_nested_json_is_rejected_before_persistence():
    from fastapi.testclient import TestClient
    from app.main import app
    from authhelper import agent_auth
    import uuid
    import json
    caller = "finite-" + uuid.uuid4().hex
    body = {"caller": caller, "audience": "owner", "title": "Synthetic finite JSON test", "control": "radio",
            "options_or_fields": [{"id": "a", "label": "A"}], "suggested": {"option_id": "a"},
            "deliver_to": {"mode": "poll"}, "zoom": {"value": float("nan")}}
    response = TestClient(app).post("/v1/requests", content=json.dumps(body),
        headers={**agent_auth(caller), "Content-Type": "application/json"})
    assert response.status_code == 422
