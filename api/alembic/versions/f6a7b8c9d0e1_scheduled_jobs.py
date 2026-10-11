"""HCA-P20 Day 3: scheduled_jobs (app/jobs.py), leased escalation and expiry.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""

import sqlalchemy as sa
from alembic import op

revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scheduled_jobs',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('ask_id', sa.String(), sa.ForeignKey('asks.id'), nullable=False, index=True),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('run_at', sa.DateTime(), nullable=False, index=True),
        sa.Column('lease_owner', sa.String(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('done_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('scheduled_jobs')
