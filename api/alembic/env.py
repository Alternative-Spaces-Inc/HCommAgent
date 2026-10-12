import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.exc import OperationalError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base
from app import models  # noqa: F401  (registers models on Base.metadata)
from app import limits  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = os.environ.get("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


LOCK_TIMEOUT = os.environ.get("HCA_MIGRATE_LOCK_TIMEOUT", "60s")
IDLE_TXN_GRACE = os.environ.get("HCA_MIGRATE_IDLE_TXN_GRACE", "30 seconds")


def _is_postgres(connection) -> bool:
    return connection.dialect.name == "postgresql"


def _terminate_idle_in_transaction(connection) -> None:
    """HCA-P19 hotfix: a backend sitting "idle in transaction" holds its
    locks until it disconnects, and DDL on the same table waits forever (the
    2026-10-10 P19 Day 1 migration hung 10 minutes behind the API's open SSE
    streams and was killed by the job timeout). Before any migration runs,
    list and terminate such backends on this database older than the grace
    period. The API reconnects on its next query (pool_pre_ping) and SSE
    clients reconnect on their own; no data is touched. Disable with
    HCA_MIGRATE_TERMINATE_IDLE=0."""
    if os.environ.get("HCA_MIGRATE_TERMINATE_IDLE", "0") != "1":
        print("migrate: idle-in-transaction termination disabled")
        return
    if not IDLE_TXN_GRACE.endswith(" seconds") or not IDLE_TXN_GRACE[:-8].isdigit():
        raise RuntimeError("HCA_MIGRATE_IDLE_TXN_GRACE must be integer seconds")
    rows = connection.execute(text("""
        SELECT pid, application_name, state, now() - state_change AS idle_for, left(query, 120) AS query
        FROM pg_stat_activity
        WHERE datname = current_database() AND pid <> pg_backend_pid()
          AND state = 'idle in transaction' AND application_name = 'hca-api'
          AND state_change < now() - CAST(:grace AS interval)
    """), {"grace": IDLE_TXN_GRACE}).fetchall()
    if not rows:
        print("migrate: no idle-in-transaction backends to clear")
        return
    for r in rows:
        print(f"migrate: terminating scoped backend pid={r.pid} idle_for={r.idle_for}")
        connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": r.pid})


def _print_blockers(connection) -> None:
    rows = connection.execute(text("""
        SELECT pid, state, application_name, now() - xact_start AS txn_age, left(query, 120) AS query
        FROM pg_stat_activity
        WHERE datname = current_database() AND pid <> pg_backend_pid() AND xact_start IS NOT NULL
        ORDER BY xact_start
    """)).fetchall()
    print(f"migrate: lock timeout after {LOCK_TIMEOUT}; {len(rows)} other backends in a transaction:")
    for r in rows:
        print(f"  pid={r.pid} state={r.state!r} app={r.application_name!r} txn_age={r.txn_age}")


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        if _is_postgres(connection):
            # One committed transaction for the setup. The SET must sit inside
            # it: executed on its own it autobegins a transaction (SQLAlchemy
            # 2.0) that Alembic then joins instead of opening its own, and the
            # whole upgrade is rolled back when the connection closes while
            # the log still reads "Running upgrade" (build 2eb3986f,
            # 2026-10-10: migrate step green, tenants table absent, revision
            # 00051 failed to start). SET without LOCAL is session scoped and
            # survives the commit, so lock_timeout still applies to the DDL.
            with connection.begin():
                _terminate_idle_in_transaction(connection)
                connection.execute(text("SELECT set_config('lock_timeout', :timeout, false)"), {"timeout": LOCK_TIMEOUT})
            assert not connection.in_transaction(), "migration setup left a transaction open"
        context.configure(connection=connection, target_metadata=target_metadata)
        try:
            with context.begin_transaction():
                context.run_migrations()
        except OperationalError as e:
            if _is_postgres(connection) and "lock timeout" in str(e).lower():
                connection.rollback()
                _print_blockers(connection)
            raise


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
