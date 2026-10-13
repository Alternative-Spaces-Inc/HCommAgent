"""HCA-P21b Day 2: per key rate_per_minute and asks_per_minute, the tenant
asks_per_day cap, 429 shape, X-RateLimit headers, limit_hit events, metrics.

Decision 9 (docs/decisions/DECISIONS_20261012.md): per key ceilings in one
shared database counter, 429 with Retry-After and X-RateLimit headers.
"""
import os
import sys
import threading
import time
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import limits, models
from app.db import SessionLocal
from app.main import app
from app.retention import cleanup
from authhelper import ADMIN_AUTH


client = TestClient(app)


def _issue(caller, **fields):
    resp = client.post("/v1/api-keys", json={"caller": caller, **fields}, headers=ADMIN_AUTH)
    assert resp.status_code == 201, resp.text
    issued = resp.json()
    return issued, {"Authorization": f"Bearer {issued['api_key']}"}


def _ask(caller):
    return {"caller": caller, "audience": "owner", "title": "Rate limit proof", "control": "chat",
            "options_or_fields": [], "suggested": {}, "deliver_to": {"mode": "poll"}}


def _events(prefix):
    return client.get(f"/v1/api-keys/{prefix}/events", headers=ADMIN_AUTH).json()


def test_issue_defaults_and_explicit_ceilings_are_visible():
    issued, _ = _issue("limits-default-" + uuid.uuid4().hex[:6])
    assert issued["rate_per_minute"] == 600 and issued["asks_per_minute"] == 60
    issued, _ = _issue("limits-explicit-" + uuid.uuid4().hex[:6], rate_per_minute=5, asks_per_minute=2)
    assert issued["rate_per_minute"] == 5 and issued["asks_per_minute"] == 2
    listed = [k for k in client.get("/v1/api-keys", headers=ADMIN_AUTH).json() if k["prefix"] == issued["prefix"]]
    assert listed[0]["rate_per_minute"] == 5 and listed[0]["asks_per_minute"] == 2
    assert _events(issued["prefix"])[0]["detail"]["rate_per_minute"] == 5
    for bad in ({"rate_per_minute": 0}, {"asks_per_minute": -1}, {"rate_per_minute": 10 ** 9}):
        assert client.post("/v1/api-keys", json={"caller": "limits-bad", **bad}, headers=ADMIN_AUTH).status_code == 422


def test_rate_per_minute_counts_down_in_headers_then_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(limits, "time", SimpleNamespace(time=lambda: 1_000_000))
    caller = "limits-rate-" + uuid.uuid4().hex[:6]
    issued, agent = _issue(caller, rate_per_minute=3)
    for remaining in ("2", "1", "0"):
        resp = client.get("/v1/requests/does-not-exist", headers=agent)
        assert resp.status_code == 404
        assert resp.headers["x-ratelimit-limit"] == "3" and resp.headers["x-ratelimit-remaining"] == remaining
    limited = client.get("/v1/requests/does-not-exist", headers=agent)
    assert limited.status_code == 429
    assert limited.json()["detail"] == "api key rate limit exceeded"
    assert limited.headers["retry-after"] == str(60 - 1_000_000 % 60)
    assert limited.headers["x-ratelimit-limit"] == "3" and limited.headers["x-ratelimit-remaining"] == "0"
    # One limit_hit event per key per window, however long the flood.
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 429
    hits = [e for e in _events(issued["prefix"]) if e["action"] == "limit_hit"]
    assert len(hits) == 1 and hits[0]["detail"] == {"limit": "rate_per_minute", "ceiling": 3}
    assert hits[0]["actor"] == "agent:" + issued["prefix"]
    # The next minute window starts fresh.
    monkeypatch.setattr(limits, "time", SimpleNamespace(time=lambda: 1_000_060))
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 404


def test_asks_per_minute_limits_posts_only_and_reads_keep_working(monkeypatch):
    monkeypatch.setattr(limits, "time", SimpleNamespace(time=lambda: 2_000_000))
    caller = "limits-asks-" + uuid.uuid4().hex[:6]
    issued, agent = _issue(caller, asks_per_minute=2)
    assert client.post("/v1/requests", json=_ask(caller), headers=agent).status_code == 201
    assert client.post("/v1/requests", json=_ask(caller), headers=agent).status_code == 201
    third = client.post("/v1/requests", json=_ask(caller), headers=agent)
    assert third.status_code == 429 and third.json()["detail"] == "api key asks per minute limit exceeded"
    assert third.headers["x-ratelimit-limit"] == "2" and third.headers["retry-after"] == str(60 - 2_000_000 % 60)
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 404
    hits = [e for e in _events(issued["prefix"]) if e["action"] == "limit_hit"]
    assert [h["detail"]["limit"] for h in hits] == ["asks_per_minute"]


def test_tenant_asks_per_day_caps_every_key_of_the_tenant(monkeypatch):
    # Real clock (frozen): the daily bucket must outlive retention.cleanup,
    # which prunes by the real current minute.
    now = int(time.time())
    monkeypatch.setattr(limits, "time", SimpleNamespace(time=lambda: now))
    slug = "daily-" + uuid.uuid4().hex[:6]
    created = client.post("/v1/tenants", json={"slug": slug, "name": slug}, headers=ADMIN_AUTH)
    assert created.status_code == 201 and created.json()["asks_per_day"] == 0
    tenant_id = created.json()["tenant_id"]
    assert client.patch(f"/v1/tenants/{tenant_id}", json={"asks_per_day": 2}).status_code == 401
    assert client.patch(f"/v1/tenants/{tenant_id}", json={"asks_per_day": -1}, headers=ADMIN_AUTH).status_code == 422
    assert client.patch("/v1/tenants/nosuch", json={"asks_per_day": 2}, headers=ADMIN_AUTH).status_code == 404
    patched = client.patch(f"/v1/tenants/{tenant_id}", json={"asks_per_day": 2}, headers=ADMIN_AUTH)
    assert patched.status_code == 200 and patched.json()["asks_per_day"] == 2

    caller_a, caller_b = slug + "-a", slug + "-b"
    _, agent_a = _issue(caller_a, tenant_id=tenant_id)
    issued_b, agent_b = _issue(caller_b, tenant_id=tenant_id)
    assert client.post("/v1/requests", json=_ask(caller_a), headers=agent_a).status_code == 201
    assert client.post("/v1/requests", json=_ask(caller_b), headers=agent_b).status_code == 201
    capped = client.post("/v1/requests", json=_ask(caller_b), headers=agent_b)
    assert capped.status_code == 429 and capped.json()["detail"] == "tenant asks per day limit exceeded"
    assert capped.headers["retry-after"] == str((now // 86400 + 1) * 86400 - now)
    assert capped.headers["x-ratelimit-limit"] == "2"
    hits = [e for e in _events(issued_b["prefix"]) if e["action"] == "limit_hit"]
    assert hits and hits[0]["detail"] == {"limit": "asks_per_day", "ceiling": 2}

    # The daily bucket survives the minute based retention sweep until the day ends.
    with SessionLocal() as db:
        cleanup(db)
        db.commit()
    assert client.post("/v1/requests", json=_ask(caller_a), headers=agent_a).status_code == 429
    # Lifting the cap (0) stops counting at once.
    assert client.patch(f"/v1/tenants/{tenant_id}", json={"asks_per_day": 0}, headers=ADMIN_AUTH).json()["asks_per_day"] == 0
    assert client.post("/v1/requests", json=_ask(caller_a), headers=agent_a).status_code == 201


def test_daily_window_is_the_last_minute_of_the_utc_day():
    identity = uuid.uuid4().hex
    now = 4_000_000
    with SessionLocal() as db:
        assert limits.consume_daily(db, identity, 1, now=now) == 0
        db.commit()
        try:
            limits.consume_daily(db, identity, 1, now=now + 100)
            raise AssertionError("second call must be refused")
        except HTTPException as exc:
            assert exc.status_code == 429
        import hashlib
        digest = hashlib.sha256(identity.encode()).hexdigest()
        row = db.execute(select(limits.RateLimitBucket).where(limits.RateLimitBucket.identity == digest)).scalar_one()
        assert row.window == (now // 86400 + 1) * 1440 - 1 and row.count == 2
        # Next day starts fresh; a ceiling of 0 counts nothing.
        assert limits.consume_daily(db, identity, 1, now=(now // 86400 + 1) * 86400) == 0
        assert limits.consume_daily(db, identity, 0, now=now) is None


def test_two_threads_racing_one_key_never_exceed_its_ceiling(monkeypatch):
    monkeypatch.setattr(limits, "time", SimpleNamespace(time=lambda: 5_000_000))
    caller = "limits-race-" + uuid.uuid4().hex[:6]
    _, agent = _issue(caller, rate_per_minute=4)
    barrier = threading.Barrier(8)
    outcomes = []

    def run():
        barrier.wait(5)
        outcomes.append(client.get("/v1/requests/does-not-exist", headers=agent).status_code)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(outcomes) == [404] * 4 + [429] * 4


def test_patch_ceilings_records_limits_changed_and_applies_at_once(monkeypatch):
    monkeypatch.setattr(limits, "time", SimpleNamespace(time=lambda: 6_000_000))
    caller = "limits-patch-" + uuid.uuid4().hex[:6]
    issued, agent = _issue(caller)
    prefix = issued["prefix"]
    assert client.patch(f"/v1/api-keys/{prefix}", json={}, headers=ADMIN_AUTH).status_code == 422
    patched = client.patch(f"/v1/api-keys/{prefix}", json={"rate_per_minute": 1}, headers=ADMIN_AUTH)
    assert patched.status_code == 200 and patched.json()["rate_per_minute"] == 1 and patched.json()["scopes"] == list(limits.__dict__.get("SCOPES", issued["scopes"]))
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 404
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 429
    # Back to the deployment default: explicit null.
    reset = client.patch(f"/v1/api-keys/{prefix}", json={"rate_per_minute": None}, headers=ADMIN_AUTH)
    assert reset.status_code == 200 and reset.json()["rate_per_minute"] == 600
    actions = [e["action"] for e in _events(prefix)]
    assert actions == ["limits_changed", "limit_hit", "limits_changed", "issued"]
    changed = [e for e in _events(prefix) if e["action"] == "limits_changed"]
    assert changed[-1]["detail"] == {"before": {"rate_per_minute": None, "asks_per_minute": None},
                                     "after": {"rate_per_minute": 1, "asks_per_minute": None}}
    # Scopes and ceilings in one PATCH write two events; a no-op writes none.
    both = client.patch(f"/v1/api-keys/{prefix}", json={"scopes": ["asks:read"], "asks_per_minute": 7}, headers=ADMIN_AUTH)
    assert both.status_code == 200 and both.json()["asks_per_minute"] == 7 and both.json()["scopes"] == ["asks:read"]
    count = len(_events(prefix))
    assert client.patch(f"/v1/api-keys/{prefix}", json={"scopes": ["asks:read"], "asks_per_minute": 7}, headers=ADMIN_AUTH).status_code == 200
    assert len(_events(prefix)) == count


def test_metrics_report_key_population_and_recent_limit_hits(monkeypatch):
    monkeypatch.setattr(limits, "time", SimpleNamespace(time=lambda: 7_000_000))
    caller = "limits-metrics-" + uuid.uuid4().hex[:6]
    _, agent = _issue(caller, rate_per_minute=1)
    client.get("/v1/requests/does-not-exist", headers=agent)
    assert client.get("/v1/requests/does-not-exist", headers=agent).status_code == 429
    with SessionLocal() as db:
        from app.metrics import collect
        block = collect(db)["api_keys"]
    assert block["active"] >= 1 and block["revoked"] >= 0 and block["rate_limited_last_hour"] >= 1
    with SessionLocal() as db:
        stored = db.execute(select(models.ApiKeyEvent).where(models.ApiKeyEvent.action == "limit_hit")).scalars().all()
        assert stored
