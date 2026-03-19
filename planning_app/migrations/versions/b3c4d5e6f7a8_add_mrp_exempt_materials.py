"""add mrp_exempt_materials table

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-03-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7a8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mrp_exempt_materials',
        sa.Column('material_code', sa.String(50), primary_key=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('exempted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('exempted_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )


def downgrade():
    op.drop_table('mrp_exempt_materials')
