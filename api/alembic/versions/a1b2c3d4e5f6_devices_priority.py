"""devices: surface ladder priority (HCA-P14, app/routing.py).

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
Create Date: 2026-10-05 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('devices') as batch:
        batch.add_column(sa.Column('priority', sa.Integer(), nullable=False, server_default='100'))


def downgrade():
    with op.batch_alter_table('devices') as batch:
        batch.drop_column('priority')
