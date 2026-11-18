"""Retain local VA05 observations and append-only quality review evidence."""

from alembic import op
import sqlalchemy as sa

revision = "c92e7a14d6b8"
down_revision = "b84f6d2a91c3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forecast_data_extracts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_by_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("rules_version", sa.String(40), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_settings", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["captured_by_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "forecast_data_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("extract_id", sa.Integer(), nullable=False),
        sa.Column("source_row_id", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("has_findings", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["extract_id"],
            ["forecast_data_extracts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "extract_id",
            "source_row_id",
            name="uq_forecast_extract_source_row",
        ),
    )
    op.create_index(
        "ix_forecast_data_records_extract_id",
        "forecast_data_records",
        ["extract_id"],
    )
    op.create_table(
        "forecast_data_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["forecast_data_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('investigate', 'accepted', 'source_correction')",
            name="ck_forecast_data_decision_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_forecast_data_decisions_record_id",
        "forecast_data_decisions",
        ["record_id"],
    )


def downgrade():
    op.drop_index(
        "ix_forecast_data_decisions_record_id",
        table_name="forecast_data_decisions",
    )
    op.drop_table("forecast_data_decisions")
    op.drop_index(
        "ix_forecast_data_records_extract_id",
        table_name="forecast_data_records",
    )
    op.drop_table("forecast_data_records")
    op.drop_table("forecast_data_extracts")
