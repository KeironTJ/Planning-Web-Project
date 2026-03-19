"""add production_ready_date and customer_hold to sales_order_lines

Revision ID: a1b2c3d4e5f6
Revises: f511ef33295b
Create Date: 2026-03-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'f511ef33295b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sales_order_lines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('production_ready_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('customer_hold', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('customer_hold_since', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('customer_hold_note', sa.String(length=255), nullable=True))
        batch_op.create_index(batch_op.f('ix_sales_order_lines_production_ready_date'), ['production_ready_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_sales_order_lines_customer_hold'), ['customer_hold'], unique=False)


def downgrade():
    with op.batch_alter_table('sales_order_lines', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sales_order_lines_customer_hold'))
        batch_op.drop_index(batch_op.f('ix_sales_order_lines_production_ready_date'))
        batch_op.drop_column('customer_hold_note')
        batch_op.drop_column('customer_hold_since')
        batch_op.drop_column('customer_hold')
        batch_op.drop_column('production_ready_date')
