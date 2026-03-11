"""add planned_date to sales_order_lines for operation-less orders

Revision ID: b2f3a4c5d6e7
Revises: a1e2f3b4c5d6
Create Date: 2026-03-11 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2f3a4c5d6e7'
down_revision = 'a1e2f3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sales_order_lines') as batch_op:
        batch_op.add_column(sa.Column('planned_date', sa.Date(), nullable=True))
        batch_op.create_index('ix_sales_order_lines_planned_date', ['planned_date'])


def downgrade():
    with op.batch_alter_table('sales_order_lines') as batch_op:
        batch_op.drop_index('ix_sales_order_lines_planned_date')
        batch_op.drop_column('planned_date')
