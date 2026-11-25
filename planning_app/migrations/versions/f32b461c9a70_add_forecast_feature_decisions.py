"""Add append-only forecasting feature assessments."""

from alembic import op
import sqlalchemy as sa

revision = "f32b461c9a70"
down_revision = "be49c128a671"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forecast_feature_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("extract_id", sa.Integer(), nullable=False),
        sa.Column("feature", sa.String(120), nullable=False),
        sa.Column("feature_key", sa.String(480), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("availability", sa.String(20), nullable=False),
        sa.Column("coverage", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("cutoff_details", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('test', 'defer', 'reject')", name="ck_forecast_feature_decision"),
        sa.CheckConstraint(
            "availability IN ('known', 'unknown', 'unavailable')",
            name="ck_forecast_feature_availability",
        ),
        sa.ForeignKeyConstraint(["extract_id"], ["forecast_data_extracts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_forecast_feature_decisions_extract_id", "forecast_feature_decisions", ["extract_id"],
    )


def downgrade():
    op.drop_index("ix_forecast_feature_decisions_extract_id", table_name="forecast_feature_decisions")
    op.drop_table("forecast_feature_decisions")
