"""HCA-P21a Day 2: webhook_deliveries outbox, encrypted destination secrets,
ref_id on scheduled_jobs, delivery_id on webhook_attempts.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('ask_id', sa.String(), sa.ForeignKey('asks.id'), nullable=False),
        sa.Column('destination_id', sa.String(), sa.ForeignKey('webhook_destinations.id'), nullable=False),
        sa.Column('event', sa.String(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('dead_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(), nullable=True),
    )
    op.create_index('ix_webhook_deliveries_tenant_id', 'webhook_deliveries', ['tenant_id'])
    op.create_index('ix_webhook_deliveries_ask_id', 'webhook_deliveries', ['ask_id'])
    op.create_index('ix_webhook_deliveries_destination_id', 'webhook_deliveries', ['destination_id'])
    op.create_index('ix_webhook_deliveries_state', 'webhook_deliveries', ['state'])
    with op.batch_alter_table('webhook_destinations') as b:
        b.add_column(sa.Column('secret_enc', sa.String(), nullable=True))
        b.add_column(sa.Column('previous_secret_enc', sa.String(), nullable=True))
    with op.batch_alter_table('scheduled_jobs') as b:
        b.add_column(sa.Column('ref_id', sa.String(), nullable=True))
    op.create_index('ix_scheduled_jobs_ref_id', 'scheduled_jobs', ['ref_id'])
    with op.batch_alter_table('webhook_attempts') as b:
        b.add_column(sa.Column('delivery_id', sa.String(), nullable=True))
    op.create_index('ix_webhook_attempts_delivery_id', 'webhook_attempts', ['delivery_id'])


def downgrade() -> None:
    op.drop_index('ix_webhook_attempts_delivery_id', table_name='webhook_attempts')
    with op.batch_alter_table('webhook_attempts') as b:
        b.drop_column('delivery_id')
    op.drop_index('ix_scheduled_jobs_ref_id', table_name='scheduled_jobs')
    with op.batch_alter_table('scheduled_jobs') as b:
        b.drop_column('ref_id')
    with op.batch_alter_table('webhook_destinations') as b:
        b.drop_column('previous_secret_enc')
        b.drop_column('secret_enc')
    for ix in ('ix_webhook_deliveries_state', 'ix_webhook_deliveries_destination_id',
               'ix_webhook_deliveries_ask_id', 'ix_webhook_deliveries_tenant_id'):
        op.drop_index(ix, table_name='webhook_deliveries')
    op.drop_table('webhook_deliveries')
