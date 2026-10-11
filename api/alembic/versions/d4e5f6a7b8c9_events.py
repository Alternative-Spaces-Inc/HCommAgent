"""HCA-P20 Day 1: events (app/events.py), the durable inbox stream log.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

import sqlalchemy as sa
from alembic import op

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'events',
        sa.Column('id', sa.Integer().with_variant(sa.BigInteger(), 'postgresql'), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('event', sa.String(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('device_ids', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, index=True),
    )
    op.create_index('ix_events_tenant_id_id', 'events', ['tenant_id', 'id'])


def downgrade() -> None:
    op.drop_index('ix_events_tenant_id_id', table_name='events')
    op.drop_table('events')
