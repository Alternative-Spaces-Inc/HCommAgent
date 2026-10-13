"""api key and tenant rate limit ceilings (HCA-P21b Day 2).

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-10-13 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(sa.Column("rate_per_minute", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("asks_per_minute", sa.Integer(), nullable=True))
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(sa.Column("asks_per_day", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_column("asks_per_day")
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_column("asks_per_minute")
        batch_op.drop_column("rate_per_minute")
