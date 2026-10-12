"""Assert the database really is at the Alembic head after `alembic upgrade head`.

Why: the API tests call Base.metadata.create_all, so a migration that logs
"Running upgrade" and is then rolled back (build 2eb3986f, 2026-10-10) still
left CI green while the live migrate step produced no tables. This script
reads the version row the database holds and compares it with the head in
alembic/versions, then checks every table the models declare exists. It needs
no models-versus-DDL autogenerate comparison, so SQLite and Postgres types do
not produce noise. Exit 0 on parity, 1 with the differences listed.

Run from api/: python3 scripts/check_migrated.py
"""
import os
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import Base  # noqa: E402
from app import models  # noqa: E402, F401
from app import limits  # noqa: F401 -- include shared rate counters


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    url = os.environ.get("DATABASE_URL", "sqlite:///./hca_local.db")
    engine = create_engine(url)
    problems = []
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
        if "alembic_version" not in names:
            problems.append("alembic_version table missing: no migration has been applied")
            current = []
        else:
            current = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
        if sorted(current) != sorted(heads):
            problems.append(f"database at {current or 'nothing'}, head is {heads}")
        missing = sorted(set(Base.metadata.tables) - names)
        if missing:
            problems.append(f"tables declared by the models but absent: {missing}")
    if problems:
        print("FAIL: database is not at the migration head")
        for p in problems:
            print("  " + p)
        return 1
    print(f"OK: database at head {heads[0]} with all {len(Base.metadata.tables)} model tables present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
