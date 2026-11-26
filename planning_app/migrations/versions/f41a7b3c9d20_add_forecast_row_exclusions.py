"""Add evidence-backed individual forecast row exclusions."""

from alembic import op
import sqlalchemy as sa

revision = "f41a7b3c9d20"
down_revision = "e81ad2397b4c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forecast_row_exclusions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("extract_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('exclude', 'clear')", name="ck_forecast_row_exclusion_action"),
        sa.ForeignKeyConstraint(["extract_id"], ["forecast_data_extracts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["record_id"], ["forecast_data_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_forecast_row_exclusions_extract_id", "forecast_row_exclusions", ["extract_id"])
    op.create_index("ix_forecast_row_exclusions_record_id", "forecast_row_exclusions", ["record_id"])


def downgrade():
    op.drop_index("ix_forecast_row_exclusions_record_id", table_name="forecast_row_exclusions")
    op.drop_index("ix_forecast_row_exclusions_extract_id", table_name="forecast_row_exclusions")
    op.drop_table("forecast_row_exclusions")
