"""add new_order operation status as default before firmed

Revision ID: a1e2f3b4c5d6
Revises: 3a9c1f7e2b05
Create Date: 2026-03-11 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1e2f3b4c5d6'
down_revision = '3a9c1f7e2b05'
branch_labels = None
depends_on = None


def upgrade():
    # Change the server-side default for new rows to 'new_order'.
    # Existing 'firmed' rows are intentionally left as-is — they were
    # already acknowledged/firmed before this status was introduced.
    with op.batch_alter_table('works_order_operations') as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.String(length=20),
            server_default='new_order',
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('works_order_operations') as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.String(length=20),
            server_default='firmed',
            existing_nullable=False,
        )
    # Revert any new_order rows back to firmed
    op.execute(
        "UPDATE works_order_operations SET status = 'firmed' WHERE status = 'new_order'"
    )
