"""Allow explicitly remembered review evidence to be confirmed in later extracts."""

from alembic import op
import sqlalchemy as sa

revision = "c51e7b20d931"
down_revision = "f32b461c9a70"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("forecast_data_decisions") as batch:
        batch.add_column(sa.Column("remember", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("reused_from_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_forecast_data_decision_reused_from",
            "forecast_data_decisions", ["reused_from_id"], ["id"], ondelete="RESTRICT",
        )


def downgrade():
    with op.batch_alter_table("forecast_data_decisions") as batch:
        batch.drop_constraint("fk_forecast_data_decision_reused_from", type_="foreignkey")
        batch.drop_column("reused_from_id")
        batch.drop_column("remember")
