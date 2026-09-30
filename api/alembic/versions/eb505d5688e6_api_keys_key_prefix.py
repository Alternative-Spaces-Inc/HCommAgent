"""api_keys.key_prefix, the visible lookup prefix from the plan's key
format hca_live_<prefix>_<secret>; key_hash alone cannot be looked up
without scanning every row, so verification needs this column.

Revision ID: eb505d5688e6
Revises: 48b5b11e6e1c
Create Date: 2026-09-29 22:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'eb505d5688e6'
down_revision = '48b5b11e6e1c'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('api_keys', sa.Column('key_prefix', sa.String(), nullable=True))
    op.create_unique_constraint('uq_api_keys_key_prefix', 'api_keys', ['key_prefix'])


def downgrade():
    op.drop_constraint('uq_api_keys_key_prefix', 'api_keys', type_='unique')
    op.drop_column('api_keys', 'key_prefix')
