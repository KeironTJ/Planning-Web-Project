"""Add deduplicated transport order-readiness snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "c3e52a91b047"
down_revision = "a8c12e704b39"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transport_order_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("load_id", sa.String(100), nullable=False),
        sa.Column("order_num", sa.Integer(), nullable=False),
        sa.Column("order_line", sa.Integer(), nullable=False),
        sa.Column("rel_num", sa.Integer(), nullable=False),
        sa.Column("customer", sa.Text()),
        sa.Column("part_num", sa.Text()),
        sa.Column("part_description", sa.Text()),
        sa.Column("quantity", sa.Numeric(18, 4)),
        sa.Column("production_state", sa.String(20), nullable=False),
        sa.Column("order_statuses", sa.JSON(), nullable=False),
        sa.Column("job_statuses", sa.JSON(), nullable=False),
        sa.Column("jobs", sa.JSON(), nullable=False),
        sa.Column("locations", sa.JSON(), nullable=False),
        sa.Column("pack_refs", sa.JSON(), nullable=False),
        sa.Column("order_held", sa.Boolean()),
        sa.Column("customer_credit_hold", sa.Boolean()),
        sa.Column("so_credit_hold", sa.Boolean()),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("order_num", "order_line", "rel_num", name="uq_transport_order_release"),
    )
    op.create_index("ix_transport_order_releases_load_id", "transport_order_releases", ["load_id"])


def downgrade():
    op.drop_table("transport_order_releases")
