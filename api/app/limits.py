"""Database-shared actor and tenant rate limits, including all API instances."""
import hashlib
import os
import time

from fastapi import HTTPException
from sqlalchemy import Column, String, Integer, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import Base


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    identity = Column(String, primary_key=True)
    window = Column(Integer, primary_key=True)
    count = Column(Integer, nullable=False)


def consume(db, identity, ceiling, now=None):
    now = time.time() if now is None else now
    window = int(now) // 60
    digest = hashlib.sha256(identity.encode()).hexdigest()
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(RateLimitBucket).values(identity=digest, window=window, count=1)
    statement = statement.on_conflict_do_update(
        index_elements=[RateLimitBucket.identity, RateLimitBucket.window],
        set_={"count": RateLimitBucket.count + 1}).returning(RateLimitBucket.count)
    count = db.execute(statement).scalar_one()
    db.execute(delete(RateLimitBucket).where(RateLimitBucket.window < window - 2))
    if count > ceiling:
        db.commit()
        raise HTTPException(status_code=429, detail="rate limit exceeded",
                            headers={"Retry-After": str(60 - int(now) % 60)})


def authorize_budget(db, tenant_id, actor):
    consume(db, "tenant:" + tenant_id, int(os.environ.get("HCA_TENANT_RATE_PER_MINUTE", "3000")))
    consume(db, "actor:" + actor, int(os.environ.get("HCA_ACTOR_RATE_PER_MINUTE", "1000")))
