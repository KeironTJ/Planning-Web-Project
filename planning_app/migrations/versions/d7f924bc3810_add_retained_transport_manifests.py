"""Add retained shipped manifest contents and observation timestamps."""

from alembic import op
import sqlalchemy as sa

revision = "d7f924bc3810"
down_revision = "c3e52a91b047"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transport_manifests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("load_id", sa.String(100), nullable=False, unique=True),
        sa.Column("route", sa.String(255)),
        sa.Column("source_status", sa.String(50), nullable=False),
        sa.Column("load_date", sa.Date()),
        sa.Column("ship_date", sa.Date()),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contents_observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transport_manifests_route", "transport_manifests", ["route"])
    op.create_index("ix_transport_manifests_ship_date", "transport_manifests", ["ship_date"])
    op.create_table(
        "transport_manifest_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("manifest_id", sa.Integer(), sa.ForeignKey("transport_manifests.id"), nullable=False),
        sa.Column("order_num", sa.Integer(), nullable=False),
        sa.Column("order_line", sa.Integer(), nullable=False),
        sa.Column("rel_num", sa.Integer(), nullable=False),
        sa.Column("customer", sa.Text()),
        sa.Column("customer_po", sa.Text()),
        sa.Column("part_num", sa.Text()),
        sa.Column("part_description", sa.Text()),
        sa.Column("jobs", sa.JSON(), nullable=False),
        sa.Column("packs", sa.JSON(), nullable=False),
        sa.UniqueConstraint("manifest_id", "order_num", "order_line", "rel_num",
                            name="uq_transport_manifest_release"),
    )
    op.create_index("ix_transport_manifest_releases_manifest_id", "transport_manifest_releases", ["manifest_id"])
    op.create_index("ix_transport_manifest_releases_order_num", "transport_manifest_releases", ["order_num"])


def downgrade():
    op.drop_table("transport_manifest_releases")
    op.drop_table("transport_manifests")
