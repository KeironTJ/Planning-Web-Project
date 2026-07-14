"""drop_sales_order_lines_and_works_order_operations

Revision ID: 96d9be241aa7
Revises: 8bf01c116d63
Create Date: 2026-07-14 11:34:38.612192

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '96d9be241aa7'
down_revision = '8bf01c116d63'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('works_order_operations')
    op.drop_table('sales_order_lines')


def downgrade():
    # Recreating these tables is not supported — they have been permanently retired.
    pass
