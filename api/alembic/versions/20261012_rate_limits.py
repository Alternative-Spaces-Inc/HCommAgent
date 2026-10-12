"""Database-shared bounded rate counters."""
from alembic import op
import sqlalchemy as sa

revision = "20261012_rate_limits"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("rate_limit_buckets", sa.Column("identity", sa.String(), nullable=False),
                    sa.Column("window", sa.Integer(), nullable=False),
                    sa.Column("count", sa.Integer(), nullable=False),
                    sa.PrimaryKeyConstraint("identity", "window"))


def downgrade():
    op.drop_table("rate_limit_buckets")
