"""Add append-only comments to saved VA05 extracts."""

from alembic import op
import sqlalchemy as sa

revision = "e6d1c5f8a237"
down_revision = "c92e7a14d6b8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forecast_data_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("extract_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["extract_id"], ["forecast_data_extracts.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_forecast_data_notes_extract_id",
        "forecast_data_notes",
        ["extract_id"],
    )


def downgrade():
    op.drop_index(
        "ix_forecast_data_notes_extract_id", table_name="forecast_data_notes",
    )
    op.drop_table("forecast_data_notes")
