"""api_key_scopes and api_key_events.

Revision ID: c9d0e1f2a3b4
Revises: 20261012_rate_limits
Create Date: 2026-10-12 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "20261012_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(
            sa.Column(
                "scopes",
                sa.Text(),
                nullable=False,
                server_default='["asks:create", "asks:read", "asks:cancel", "webhooks:manage"]',
            )
        )

    op.create_table(
        "api_key_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("api_key_id", sa.String(), sa.ForeignKey("api_keys.id"), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_api_key_events_key_at", "api_key_events", ["api_key_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_api_key_events_key_at", table_name="api_key_events")
    op.drop_table("api_key_events")
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_column("scopes")
