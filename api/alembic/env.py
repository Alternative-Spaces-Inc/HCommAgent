import os
import re
import sys
import time
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.exc import OperationalError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base
from app import models  # noqa: F401  (registers models on Base.metadata)
from app import limits  # noqa: F401  (registers rate counters)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = os.environ.get("DATABASE_URL")
if db_url:
    # Alembic uses ConfigParser interpolation; preserve encoded credentials.
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _setting(name: str, default: str, pattern: str) -> str:
    """Read a migration tuning value from the environment and refuse anything
    that is not a plain duration literal, so an environment string can never
    reach SQL text unvalidated."""
    value = os.environ.get(name, default)
    if not re.fullmatch(pattern, value):
        raise SystemExit(f"migrate: {name} must match {pattern!r}, refusing to run")
    return value


def _duration(name: str, default: str, units: dict[str, int], maximum: int, separator: str = "") -> str:
    """Return a canonical literal after enforcing the duration's real unit bound."""
    value = _setting(name, default, r"[1-9][0-9]{0,5}" + separator + r"(" + "|".join(units) + r")")
    match = re.fullmatch(r"([1-9][0-9]{0,5}) ?([a-z]+)", value)
    milliseconds = int(match[1]) * units[match[2]]
    if milliseconds > maximum:
        raise SystemExit(f"migrate: {name} exceeds its bounded duration, refusing to run")
    return f"{milliseconds}ms"


LOCK_TIMEOUT = _duration("HCA_MIGRATE_LOCK_TIMEOUT", "60s", {"ms": 1, "s": 1000, "min": 60000}, 300000)
IDLE_TXN_GRACE = _duration("HCA_MIGRATE_IDLE_TXN_GRACE", "30 seconds", {"seconds": 1000, "minutes": 60000}, 86400000, " ")
SERIALIZE_SECONDS = int(_setting("HCA_MIGRATE_SERIALIZE_SECONDS", "60", r"[1-9][0-9]{0,2}"))
if SERIALIZE_SECONDS > 300:
    raise SystemExit("migrate: HCA_MIGRATE_SERIALIZE_SECONDS must be between 1 and 300, refusing to run")
MIGRATION_LOCK_ID = 0x4843414D494752


def _is_postgres(connection) -> bool:
    return connection.dialect.name == "postgresql"


def _acquire_migration_lock(connection):
    """Session lock spans setup and DDL commits; wait only for the configured budget."""
    deadline = time.monotonic() + SERIALIZE_SECONDS
    while True:
        acquired = connection.execute(text("SELECT pg_try_advisory_lock(:lock_id)"),
                                      {"lock_id": MIGRATION_LOCK_ID}).scalar()
        connection.commit()
        if acquired:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("migrate: another migration holds the lock; retry a new build after it completes")
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def _release_migration_lock(connection):
    if connection.in_transaction():
        connection.rollback()
    connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": MIGRATION_LOCK_ID})
    connection.commit()


def _terminate_idle_in_transaction(connection) -> None:
    """HCA-P19 hotfix: a backend sitting "idle in transaction" holds its
    locks until it disconnects, and DDL on the same table waits forever (the
    2026-10-10 P19 Day 1 migration hung 10 minutes behind the API's open SSE
    streams and was killed by the job timeout). Before any migration runs,
    list and terminate such backends on this database older than the grace
    period. Recovery is an explicit operator action, scoped to both the API
    application name and the configured runtime database role. Normal builds
    do not terminate sessions."""
    if os.environ.get("HCA_MIGRATE_TERMINATE_IDLE", "0") != "1":
        print("migrate: idle-in-transaction termination disabled")
        return
    runtime_role = _setting("HCA_MIGRATE_RECOVERY_ROLE", "", r"[A-Za-z_][A-Za-z0-9_]{0,62}")
    rows = connection.execute(text("""
        SELECT pid, application_name, state, now() - state_change AS idle_for
        FROM pg_stat_activity
        WHERE datname = current_database() AND pid <> pg_backend_pid()
          AND state = 'idle in transaction'
          AND state_change < now() - CAST(:grace AS interval)
          AND application_name = 'hca-api' AND usename = :runtime_role
    """), {"grace": IDLE_TXN_GRACE, "runtime_role": runtime_role}).fetchall()
    if not rows:
        print("migrate: no idle-in-transaction API backends to clear")
        return
    for r in rows:
        # Query text is not logged: it can carry row values.
        print(f"migrate: terminating idle-in-transaction backend pid={r.pid} app={r.application_name!r} idle_for={r.idle_for}")
        connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": r.pid})


def _print_blockers(connection) -> None:
    rows = connection.execute(text("""
        SELECT pid, state, application_name, now() - xact_start AS txn_age
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
        locked = False
        if _is_postgres(connection):
            _acquire_migration_lock(connection)
            locked = True
            # One committed transaction for the setup. The SET must sit inside
            # it: executed on its own it autobegins a transaction (SQLAlchemy
            # 2.0) that Alembic then joins instead of opening its own, and the
            # whole upgrade is rolled back when the connection closes while
            # the log still reads "Running upgrade" (build 2eb3986f,
            # 2026-10-10: migrate step green, tenants table absent, revision
            # 00051 failed to start). SET without LOCAL is session scoped and
            # survives the commit, so lock_timeout still applies to the DDL.
        try:
            if _is_postgres(connection):
                with connection.begin():
                    _terminate_idle_in_transaction(connection)
                    connection.execute(text("SELECT set_config('lock_timeout', :timeout, false)"), {"timeout": LOCK_TIMEOUT})
                assert not connection.in_transaction(), "migration setup left a transaction open"
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        except OperationalError as e:
            if _is_postgres(connection) and "lock timeout" in str(e).lower():
                connection.rollback()
                _print_blockers(connection)
            raise
        finally:
            if locked:
                _release_migration_lock(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
