"""add material_group to material_requirements

Revision ID: b7f3e2a1d4c9
Revises: 4a28c5df2031
Create Date: 2026-08-18

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7f3e2a1d4c9'
down_revision = '1dd4152c34da'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('material_requirements', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'material_group',
            sa.String(length=20),
            nullable=False,
            server_default='fabric',
        ))
        batch_op.create_index(
            'ix_material_requirements_material_group',
            ['material_group'],
        )


def downgrade():
    with op.batch_alter_table('material_requirements', schema=None) as batch_op:
        batch_op.drop_index('ix_material_requirements_material_group')
        batch_op.drop_column('material_group')
