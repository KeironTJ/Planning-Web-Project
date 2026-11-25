"""Persist development model comparisons."""

from alembic import op
import sqlalchemy as sa

revision = "be49c128a671"
down_revision = "e6d1c5f8a237"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forecast_experiments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("extract_id", sa.Integer(), nullable=False),
        sa.Column("extract_hash", sa.String(64), nullable=False),
        sa.Column("requested_by_id", sa.Integer()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("active_key", sa.String(20), unique=True),
        sa.Column("error", sa.Text()),
        sa.Column("result", sa.JSON()),
        sa.ForeignKeyConstraint(["extract_id"], ["forecast_data_extracts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_forecast_experiments_extract_id", "forecast_experiments", ["extract_id"])


def downgrade():
    op.drop_index("ix_forecast_experiments_extract_id", table_name="forecast_experiments")
    op.drop_table("forecast_experiments")
