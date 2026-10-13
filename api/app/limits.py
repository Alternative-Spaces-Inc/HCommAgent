"""Database-shared rate limits for every API instance.

One fixed window counter table (rate_limit_buckets) incremented with a single
INSERT ... ON CONFLICT DO UPDATE ... RETURNING statement, so the Cloud Run
instances share one count and the hot path is one round trip. Windows:

- per minute: window = epoch minute. Tenant and actor ceilings come from the
  environment; HCA-P21b adds per key ceilings stored on the key row
  (api_keys.rate_per_minute for every authenticated call,
  api_keys.asks_per_minute for POST /v1/requests).
- per UTC day (HCA-P21b tenants.asks_per_day): window = the last epoch minute
  of that day, so retention.cleanup (which drops windows older than the
  current minute minus two) only removes a daily bucket once the day is over.

Over the limit answers 429 with Retry-After seconds to the window end and the
X-RateLimit-Limit and X-RateLimit-Remaining headers; successful agent key
calls carry the same two headers through request.state.rate_limit (see
RateLimitHeaders in middleware.py).
"""
import hashlib
import os
import time

from fastapi import HTTPException
from sqlalchemy import Column, String, Integer
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import Base

DAY_SECONDS = 86400
DEFAULT_KEY_RATE_PER_MINUTE = 600
DEFAULT_KEY_ASKS_PER_MINUTE = 60


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    identity = Column(String, primary_key=True)
    window = Column(Integer, primary_key=True)
    count = Column(Integer, nullable=False)


def _increment(db, identity, window):
    digest = hashlib.sha256(identity.encode()).hexdigest()
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(RateLimitBucket).values(identity=digest, window=window, count=1)
    statement = statement.on_conflict_do_update(
        index_elements=[RateLimitBucket.identity, RateLimitBucket.window],
        set_={"count": RateLimitBucket.count + 1}).returning(RateLimitBucket.count)
    return int(db.execute(statement).scalar_one())


def _headers(ceiling, remaining, retry_after=None):
    headers = {"X-RateLimit-Limit": str(ceiling), "X-RateLimit-Remaining": str(max(0, remaining))}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return headers


def consume(db, identity, ceiling, now=None, limit_name="rate limit", on_hit=None):
    """Counts one call against identity in the current minute window and
    raises 429 past the ceiling. Returns the remaining budget. on_hit(db) is
    called once per window when the ceiling is first crossed (used for the
    limit_hit api key event), before the commit that persists the count."""
    now = time.time() if now is None else now
    window = int(now) // 60
    count = _increment(db, identity, window)
    remaining = ceiling - count
    if count > ceiling:
        retry_after = 60 - int(now) % 60
        if on_hit is not None and count == ceiling + 1:
            on_hit(db)
        db.commit()
        raise HTTPException(status_code=429, detail=f"{limit_name} exceeded",
                            headers=_headers(ceiling, 0, retry_after))
    return remaining


def consume_daily(db, identity, ceiling, now=None, limit_name="daily limit", on_hit=None):
    """Same contract as consume for a UTC day window; a ceiling of 0 means
    unlimited and counts nothing."""
    if ceiling <= 0:
        return None
    now = time.time() if now is None else now
    day = int(now) // DAY_SECONDS
    window = (day + 1) * (DAY_SECONDS // 60) - 1
    count = _increment(db, identity, window)
    remaining = ceiling - count
    if count > ceiling:
        retry_after = (day + 1) * DAY_SECONDS - int(now)
        if on_hit is not None and count == ceiling + 1:
            on_hit(db)
        db.commit()
        raise HTTPException(status_code=429, detail=f"{limit_name} exceeded",
                            headers=_headers(ceiling, 0, retry_after))
    return remaining


def authorize_budget(db, tenant_id, actor):
    consume(db, "tenant:" + tenant_id, int(os.environ.get("HCA_TENANT_RATE_PER_MINUTE", "3000")), limit_name="tenant rate limit")
    consume(db, "actor:" + tenant_id + ":" + actor, int(os.environ.get("HCA_ACTOR_RATE_PER_MINUTE", "1000")), limit_name="rate limit")


def key_rate_ceiling(key) -> int:
    value = getattr(key, "rate_per_minute", None)
    return int(value) if value is not None else int(os.environ.get("HCA_KEY_RATE_PER_MINUTE", str(DEFAULT_KEY_RATE_PER_MINUTE)))


def key_asks_ceiling(key) -> int:
    value = getattr(key, "asks_per_minute", None)
    return int(value) if value is not None else int(os.environ.get("HCA_KEY_ASKS_PER_MINUTE", str(DEFAULT_KEY_ASKS_PER_MINUTE)))


def _limit_hit_recorder(key, limit_name, ceiling):
    def _record(db):
        from .apikeys import record_key_event
        record_key_event(db, key, "limit_hit", "agent:" + key.key_prefix,
                         {"limit": limit_name, "ceiling": ceiling})
    return _record


def authorize_key_budget(db, key):
    """HCA-P21b: the tenant per minute ceiling from the environment plus the
    key's own rate_per_minute. Returns (ceiling, remaining) for the headers."""
    consume(db, "tenant:" + key.tenant_id, int(os.environ.get("HCA_TENANT_RATE_PER_MINUTE", "3000")), limit_name="tenant rate limit")
    ceiling = key_rate_ceiling(key)
    remaining = consume(db, "key:" + key.tenant_id + ":" + key.key_prefix, ceiling, limit_name="api key rate limit",
                        on_hit=_limit_hit_recorder(key, "rate_per_minute", ceiling))
    return ceiling, remaining


def authorize_ask_budget(db, key, tenant):
    """HCA-P21b: POST /v1/requests only. The key's asks_per_minute, then the
    tenant's asks_per_day (0 means unlimited)."""
    ceiling = key_asks_ceiling(key)
    consume(db, "asks:" + key.tenant_id + ":" + key.key_prefix, ceiling, limit_name="api key asks per minute limit",
            on_hit=_limit_hit_recorder(key, "asks_per_minute", ceiling))
    daily = int(getattr(tenant, "asks_per_day", 0) or 0)
    consume_daily(db, "asks-day:" + key.tenant_id, daily, limit_name="tenant asks per day limit",
                  on_hit=_limit_hit_recorder(key, "asks_per_day", daily))
