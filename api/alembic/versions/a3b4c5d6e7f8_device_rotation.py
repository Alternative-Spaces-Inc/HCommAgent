"""device key rotation overlap (HCA-P23 Day 3)."""
import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("devices") as batch:
            batch.add_column(sa.Column("superseded_by", sa.String(), nullable=True))
            batch.add_column(sa.Column("previous_valid_until", sa.DateTime(), nullable=True))
            batch.create_foreign_key("fk_devices_superseded_by_devices", "devices", ["superseded_by"], ["id"])
        op.create_table("device_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("device_id", sa.String(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["device_id"], ["devices.id"]),
            sa.PrimaryKeyConstraint("id"))
        op.create_index("ix_device_events_device_at", "device_events", ["device_id", "at"])
    else:
        op.add_column("devices", sa.Column("superseded_by", sa.String(), nullable=True))
        op.add_column("devices", sa.Column("previous_valid_until", sa.DateTime(), nullable=True))
        op.create_foreign_key("fk_devices_superseded_by_devices", "devices", "devices", ["superseded_by"], ["id"])
        op.create_table("device_events",
            sa.Column("id", sa.String(), nullable=False), sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("device_id", sa.String(), nullable=False), sa.Column("action", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False), sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["device_id"], ["devices.id"]), sa.PrimaryKeyConstraint("id"))
        op.create_index("ix_device_events_device_at", "device_events", ["device_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_device_events_device_at", table_name="device_events")
    op.drop_table("device_events")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("devices") as batch:
            batch.drop_constraint("fk_devices_superseded_by_devices", type_="foreignkey")
            batch.drop_column("previous_valid_until")
            batch.drop_column("superseded_by")
    else:
        op.drop_constraint("fk_devices_superseded_by_devices", "devices", type_="foreignkey")
        op.drop_column("devices", "previous_valid_until")
        op.drop_column("devices", "superseded_by")
