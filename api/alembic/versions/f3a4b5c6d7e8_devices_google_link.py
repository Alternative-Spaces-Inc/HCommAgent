"""devices: Google account linked to a device (HCA-P7, app/googleauth.py).

Revision ID: f3a4b5c6d7e8
Revises: d7e8f9a0b1c2
Create Date: 2026-10-02 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f3a4b5c6d7e8'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('devices') as batch:
        batch.add_column(sa.Column('google_sub', sa.String(), nullable=True))
        batch.add_column(sa.Column('google_email', sa.String(), nullable=True))
        batch.add_column(sa.Column('google_linked_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('devices') as batch:
        batch.drop_column('google_linked_at')
        batch.drop_column('google_email')
        batch.drop_column('google_sub')
