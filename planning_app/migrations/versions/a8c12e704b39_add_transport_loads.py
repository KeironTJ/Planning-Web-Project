"""Add CSGTransportWB transport load snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "a8c12e704b39"
down_revision = "4c4f1305e59c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transport_loads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_row_id", sa.String(100), nullable=False, unique=True),
        sa.Column("load_id", sa.String(100), nullable=False),
        sa.Column("transport_code", sa.String(100)),
        sa.Column("route", sa.String(255)),
        sa.Column("vehicle", sa.String(255)),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("capacity", sa.Numeric(18, 4)),
        sa.Column("remaining", sa.Numeric(18, 4)),
        sa.Column("capacity_uom", sa.String(50)),
        sa.Column("available_pct", sa.Numeric(18, 4)),
        sa.Column("order_qty", sa.Numeric(18, 4)),
        sa.Column("order_value", sa.Numeric(18, 4)),
        sa.Column("load_date", sa.Date()),
        sa.Column("ship_date", sa.Date()),
        sa.Column("return_date", sa.Date()),
        sa.Column("load_time", sa.String(30)),
        sa.Column("ship_time", sa.String(30)),
        sa.Column("return_time", sa.String(30)),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("load_id", "status", "ship_date"):
        op.create_index(f"ix_transport_loads_{column}", "transport_loads", [column])


def downgrade():
    op.drop_table("transport_loads")
