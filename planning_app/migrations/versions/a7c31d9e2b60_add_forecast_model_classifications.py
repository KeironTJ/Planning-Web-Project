"""Add persistent exact model classifications for forecasting preparation."""

from alembic import op
import sqlalchemy as sa

revision = "a7c31d9e2b60"
down_revision = "6f2c9a1d4e80"
branch_labels = None
depends_on = None


def upgrade():
    """Create decisions separately from the refreshed works-order table."""
    op.create_table(
        "forecast_model_classifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('full_unit', 'cover_only', 'scatter', 'excluded')",
            name="ck_forecast_model_category",
        ),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model"),
    )


def downgrade():
    """Remove forecasting decisions without changing operational data."""
    op.drop_table("forecast_model_classifications")
