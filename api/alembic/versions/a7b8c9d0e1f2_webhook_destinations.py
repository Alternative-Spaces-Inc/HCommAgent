"""HCA-P21a: webhook_destinations and webhook_destination_events

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
import sqlalchemy as sa
from alembic import op

revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'webhook_destinations',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('caller', sa.String(), nullable=False, index=True),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('secret_prefix', sa.String(), nullable=False),
        sa.Column('secret_hash', sa.String(), nullable=False),
        sa.Column('previous_secret_hash', sa.String(), nullable=True),
        sa.Column('previous_valid_until', sa.DateTime(), nullable=True),
        sa.Column('pinned_addresses', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('rotated_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'webhook_destination_events',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('destination_id', sa.String(), sa.ForeignKey('webhook_destinations.id'), nullable=False, index=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('actor', sa.String(), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('webhook_destination_events')
    op.drop_table('webhook_destinations')
