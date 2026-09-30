"""devices: registered human surface devices whose ECDSA P-256 public keys
sign every call to the human routes (app/devicesig.py, HCA-P6).

Revision ID: d7e8f9a0b1c2
Revises: c1d2e3f4a5b6
Create Date: 2026-09-30 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd7e8f9a0b1c2'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'devices',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('surface', sa.String(), nullable=False, server_default='browser_overlay'),
        sa.Column('public_key_pem', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('devices')
