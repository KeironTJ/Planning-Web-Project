"""add last_modified fields to smv_matrix

Revision ID: c3d4e5f6a7b8
Revises: b2f3a4c5d6e7
Create Date: 2026-03-11 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2f3a4c5d6e7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('smv_matrix') as batch_op:
        batch_op.add_column(sa.Column('last_modified_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('last_modified_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_smv_matrix_last_modified_by',
            'users',
            ['last_modified_by_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade():
    with op.batch_alter_table('smv_matrix') as batch_op:
        batch_op.drop_constraint('fk_smv_matrix_last_modified_by', type_='foreignkey')
        batch_op.drop_column('last_modified_by_id')
        batch_op.drop_column('last_modified_at')
