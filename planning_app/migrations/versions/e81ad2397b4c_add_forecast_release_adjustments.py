"""Add evidence-backed forecasting release adjustments."""

from alembic import op
import sqlalchemy as sa

revision = "e81ad2397b4c"
down_revision = "c51e7b20d931"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE forecast_data_extracts ADD COLUMN derived_from_id INTEGER "
            "REFERENCES forecast_data_extracts (id) ON DELETE RESTRICT"
        )
    else:
        op.add_column("forecast_data_extracts", sa.Column("derived_from_id", sa.Integer()))
        op.create_foreign_key(
            "fk_forecast_extract_derived_from", "forecast_data_extracts", "forecast_data_extracts",
            ["derived_from_id"], ["id"], ondelete="RESTRICT",
        )
    op.create_index("ix_forecast_data_extracts_derived_from_id", "forecast_data_extracts", ["derived_from_id"])
    op.create_table(
        "forecast_release_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("extract_id", sa.Integer(), nullable=False),
        sa.Column("order_num", sa.Integer(), nullable=False),
        sa.Column("order_line", sa.Integer(), nullable=False),
        sa.Column("rel_num", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("corrections", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("order_signature", sa.String(64), nullable=False),
        sa.Column("reused_from_id", sa.Integer()),
        sa.Column("created_by_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('correct', 'exclude', 'clear')", name="ck_forecast_adjustment_action"),
        sa.ForeignKeyConstraint(["extract_id"], ["forecast_data_extracts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reused_from_id"], ["forecast_release_adjustments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_forecast_release_adjustments_extract_id", "forecast_release_adjustments", ["extract_id"])


def downgrade():
    op.drop_index("ix_forecast_release_adjustments_extract_id", table_name="forecast_release_adjustments")
    op.drop_table("forecast_release_adjustments")
    op.drop_index("ix_forecast_data_extracts_derived_from_id", table_name="forecast_data_extracts")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_forecast_extract_derived_from", "forecast_data_extracts", type_="foreignkey")
    op.drop_column("forecast_data_extracts", "derived_from_id")
