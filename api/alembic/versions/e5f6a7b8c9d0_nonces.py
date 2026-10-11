"""HCA-P20 Day 2: nonces (app/nonces.py), signature replay guard shared by instances.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

import sqlalchemy as sa
from alembic import op

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'nonces',
        sa.Column('device_id', sa.String(), primary_key=True),
        sa.Column('nonce', sa.String(), primary_key=True),
        sa.Column('seen_at', sa.DateTime(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table('nonces')
