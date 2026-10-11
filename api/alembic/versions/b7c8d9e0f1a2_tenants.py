"""HCA-P19 Day 1: tenants, tenant_owners, tenant_id on asks, devices, api_keys, audit_rows.

Existing rows are backfilled to the "default" tenant so an upgraded
one-owner deployment keeps every ask, device and key.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = 'b7c8d9e0f1a2'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

TENANTED = ('asks', 'devices', 'api_keys', 'audit_rows')


def upgrade() -> None:
    tenants = op.create_table(
        'tenants',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('slug', sa.String(), nullable=False, unique=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('disabled_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'tenant_owners',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('removed_at', sa.DateTime(), nullable=True),
    )
    op.bulk_insert(tenants, [{
        'id': 'default', 'slug': 'default', 'name': 'Default tenant',
        'created_at': datetime.now(timezone.utc).replace(tzinfo=None), 'disabled_at': None,
    }])
    for table in TENANTED:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('tenant_id', sa.String(), nullable=False, server_default='default'))
            batch.create_index(f'ix_{table}_tenant_id', ['tenant_id'])
            batch.create_foreign_key(f'fk_{table}_tenant_id_tenants', 'tenants', ['tenant_id'], ['id'])


def downgrade() -> None:
    for table in reversed(TENANTED):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f'fk_{table}_tenant_id_tenants', type_='foreignkey')
            batch.drop_index(f'ix_{table}_tenant_id')
            batch.drop_column('tenant_id')
    op.drop_table('tenant_owners')
    op.drop_table('tenants')
