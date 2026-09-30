"""webhook_attempts: per-attempt audit of outbound deliver_to webhook
posts (app/webhooks.py). One row per HTTP attempt, or one attempt-0 row
recording a skip when deliver_to is not a webhook.

Revision ID: c1d2e3f4a5b6
Revises: eb505d5688e6
Create Date: 2026-09-30 12:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c1d2e3f4a5b6'
down_revision = 'eb505d5688e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'webhook_attempts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('ask_id', sa.String(), sa.ForeignKey('asks.id'), nullable=False),
        sa.Column('event', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('ok', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('attempted_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_webhook_attempts_ask_id', 'webhook_attempts', ['ask_id'])


def downgrade():
    op.drop_index('ix_webhook_attempts_ask_id', table_name='webhook_attempts')
    op.drop_table('webhook_attempts')
