"""api_keys.key_prefix, the visible lookup prefix from the plan's key
format hca_live_<prefix>_<secret>; key_hash alone cannot be looked up
without scanning every row, so verification needs this column.

Uses batch mode: SQLite has no ALTER TABLE ADD CONSTRAINT, so a plain
op.create_unique_constraint fails there (surfaced by the sub-task 3 SSE
end to end test, the first time this migration ran against SQLite
instead of the Postgres CI job). Batch mode works on both SQLite and
Postgres, so this is the one portable form, not a Postgres-only fix.

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
    with op.batch_alter_table('api_keys') as batch_op:
        batch_op.add_column(sa.Column('key_prefix', sa.String(), nullable=True))
        batch_op.create_unique_constraint('uq_api_keys_key_prefix', ['key_prefix'])


def downgrade():
    with op.batch_alter_table('api_keys') as batch_op:
        batch_op.drop_constraint('uq_api_keys_key_prefix', type_='unique')
        batch_op.drop_column('key_prefix')
