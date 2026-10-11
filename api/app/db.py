import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./hca_local.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}


def pool_kwargs(url: str, env: dict | None = None) -> dict:
    """Connection pool bounds for a server database.

    The production database is a small Cloud SQL instance with 25 connection
    slots. SQLAlchemy's defaults (5 pooled plus 10 overflow per process, no
    recycle) let two service revisions overlapping during a deploy, the
    migrate job and the smoke test exhaust the slots; the live service then
    answered 500 with "remaining connection slots are reserved" (build
    92d896c9, 2026-10-10). Bounds are small by default and tunable by
    environment so a larger instance can raise them without a code change.
    SQLite gets no pool arguments (its pool class takes none of these).
    """
    if url.startswith("sqlite"):
        return {}
    env = os.environ if env is None else env
    return {
        "pool_size": int(env.get("HCA_DB_POOL_SIZE", "3")),
        "max_overflow": int(env.get("HCA_DB_MAX_OVERFLOW", "3")),
        "pool_timeout": int(env.get("HCA_DB_POOL_TIMEOUT", "10")),
        "pool_recycle": int(env.get("HCA_DB_POOL_RECYCLE", "300")),
    }


# pool_pre_ping: a connection the database closed (the migration job
# terminates idle-in-transaction backends before DDL) is replaced quietly
# instead of failing one request.
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, **pool_kwargs(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def db_ping() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
