"""HCA-P19 Day 2: idempotency_keys (app/idempotency.py).

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
"""

import sqlalchemy as sa
from alembic import op

revision = 'c3d4e5f6a7b8'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'idempotency_keys',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('scope', sa.String(), nullable=False),
        sa.Column('route', sa.String(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('request_hash', sa.String(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('response', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, index=True),
        sa.UniqueConstraint('tenant_id', 'scope', 'route', 'key', name='uq_idempotency_scope_key'),
    )


def downgrade() -> None:
    op.drop_table('idempotency_keys')
