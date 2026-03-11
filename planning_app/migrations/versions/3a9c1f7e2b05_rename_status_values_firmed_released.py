"""rename operation status values: not_started->firmed, started->released

Revision ID: 3a9c1f7e2b05
Revises: 128755418d31
Create Date: 2026-03-11 15:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '3a9c1f7e2b05'
down_revision = '128755418d31'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE works_order_operations SET status = 'firmed' WHERE status = 'not_started'"
    )
    op.execute(
        "UPDATE works_order_operations SET status = 'released' WHERE status = 'started'"
    )


def downgrade():
    op.execute(
        "UPDATE works_order_operations SET status = 'not_started' WHERE status = 'firmed'"
    )
    op.execute(
        "UPDATE works_order_operations SET status = 'started' WHERE status = 'released'"
    )
