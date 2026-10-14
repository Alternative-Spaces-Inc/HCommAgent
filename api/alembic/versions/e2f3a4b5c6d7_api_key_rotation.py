"""api key rotation: superseded_by, previous_valid_until, partial unique caller index (HCA-P21b Day 3).

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-10-13 00:00:00.000000

Decision 9 (docs/decisions/DECISIONS_20261012.md): a rotation writes a new
row for the same caller and marks the old row superseded with a 24 hour
previous_valid_until. The plain unique constraint on api_keys.caller is
therefore replaced by a partial unique index: one active (not revoked, not
superseded) key per caller. Postgres drops the constraint by its generated
name; SQLite rebuilds the table through batch mode.
"""
import sqlalchemy as sa
from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None

ACTIVE_INDEX = "ux_api_keys_caller_active"
ACTIVE_WHERE = "revoked_at IS NULL AND superseded_by IS NULL"


def _caller_unique_constraints(bind) -> list[str]:
    inspector = sa.inspect(bind)
    return [c["name"] for c in inspector.get_unique_constraints("api_keys")
            if c.get("column_names") == ["caller"] and c.get("name")]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("api_keys", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch_op:
            batch_op.add_column(sa.Column("superseded_by", sa.String(), nullable=True))
            batch_op.add_column(sa.Column("previous_valid_until", sa.DateTime(), nullable=True))
            batch_op.drop_constraint("uq_api_keys_caller", type_="unique")
    else:
        op.add_column("api_keys", sa.Column("superseded_by", sa.String(), nullable=True))
        op.add_column("api_keys", sa.Column("previous_valid_until", sa.DateTime(), nullable=True))
        for name in _caller_unique_constraints(bind):
            op.drop_constraint(name, "api_keys", type_="unique")
    op.create_index(ACTIVE_INDEX, "api_keys", ["caller"], unique=True,
                    postgresql_where=sa.text(ACTIVE_WHERE), sqlite_where=sa.text(ACTIVE_WHERE))


def downgrade() -> None:
    op.drop_index(ACTIVE_INDEX, table_name="api_keys")
    # Superseded rows would violate the restored constraint; keep only the
    # newest row per caller before putting the plain unique constraint back.
    op.execute(sa.text(
        "DELETE FROM api_key_events WHERE api_key_id IN (SELECT id FROM api_keys WHERE superseded_by IS NOT NULL)"))
    op.execute(sa.text("DELETE FROM api_keys WHERE superseded_by IS NOT NULL"))
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.drop_column("previous_valid_until")
            batch_op.drop_column("superseded_by")
            batch_op.create_unique_constraint("uq_api_keys_caller", ["caller"])
    else:
        op.drop_column("api_keys", "previous_valid_until")
        op.drop_column("api_keys", "superseded_by")
        op.create_unique_constraint("api_keys_caller_key", "api_keys", ["caller"])
