"""Unify operation status names: firmed->firm_planned

Revision ID: e1f2a3b4c5d6
Revises: d61b009f901f
Create Date: 2026-03-16 12:00:00.000000

Renames the 'firmed' status value to 'firm_planned' in works_order_operations
to align with the unified status vocabulary used across all levels
(SO / line / operation):

  new_order -> firm_planned -> released -> wip -> completed -> closed

The 'complete' value (line-level aggregate_status) was a computed property
only — no stored rows exist, so no DB change is needed for that rename.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e1f2a3b4c5d6'
down_revision = 'd61b009f901f'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE works_order_operations SET status = 'firm_planned' WHERE status = 'firmed'"
    )


def downgrade():
    op.execute(
        "UPDATE works_order_operations SET status = 'firmed' WHERE status = 'firm_planned'"
    )
