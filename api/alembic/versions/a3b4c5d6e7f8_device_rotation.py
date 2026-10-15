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
    else:
        op.add_column("devices", sa.Column("superseded_by", sa.String(), nullable=True))
        op.add_column("devices", sa.Column("previous_valid_until", sa.DateTime(), nullable=True))
        op.create_foreign_key("fk_devices_superseded_by_devices", "devices", "devices", ["superseded_by"], ["id"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("devices") as batch:
            batch.drop_constraint("fk_devices_superseded_by_devices", type_="foreignkey")
            batch.drop_column("previous_valid_until")
            batch.drop_column("superseded_by")
    else:
        op.drop_constraint("fk_devices_superseded_by_devices", "devices", type_="foreignkey")
        op.drop_column("devices", "previous_valid_until")
        op.drop_column("devices", "superseded_by")
