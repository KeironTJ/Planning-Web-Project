"""Link confirmed row exclusions to their earlier evidence."""

from alembic import op
import sqlalchemy as sa

revision = "b2869ef07c41"
down_revision = "f41a7b3c9d20"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE forecast_row_exclusions ADD COLUMN reused_from_id INTEGER "
            "REFERENCES forecast_row_exclusions (id) ON DELETE RESTRICT"
        )
    else:
        op.add_column("forecast_row_exclusions", sa.Column("reused_from_id", sa.Integer()))
        op.create_foreign_key(
            "fk_forecast_row_exclusion_reused_from", "forecast_row_exclusions", "forecast_row_exclusions",
            ["reused_from_id"], ["id"], ondelete="RESTRICT",
        )


def downgrade():
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_forecast_row_exclusion_reused_from", "forecast_row_exclusions", type_="foreignkey")
    op.drop_column("forecast_row_exclusions", "reused_from_id")
