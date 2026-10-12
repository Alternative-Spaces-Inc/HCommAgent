"""Preserve SQLite event sequence IDs when retention empties the log.

PostgreSQL sequences already preserve their high-water mark. SQLite's
ordinary INTEGER PRIMARY KEY reuses IDs once rows are deleted; recreate
its table with AUTOINCREMENT while preserving rows, constraints and indexes.
"""
from alembic import op

revision = "20261012_event_cursor"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("events", recreate="always", table_kwargs={"sqlite_autoincrement": True}):
            pass


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("events", recreate="always", table_kwargs={"sqlite_autoincrement": False}):
            pass
