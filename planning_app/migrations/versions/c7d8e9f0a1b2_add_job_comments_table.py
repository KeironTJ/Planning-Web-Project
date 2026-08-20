"""add job_comments table

Revision ID: c7d8e9f0a1b2
Revises: b7f3e2a1d4c9
Create Date: 2026-08-20

"""
from alembic import op
import sqlalchemy as sa

revision = 'c7d8e9f0a1b2'
down_revision = 'b7f3e2a1d4c9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'job_comments',
        sa.Column('id',         sa.Integer(),                  nullable=False),
        sa.Column('job_num',    sa.String(length=20),          nullable=False),
        sa.Column('user_id',    sa.Integer(),                  nullable=True),
        sa.Column('body',       sa.Text(),                     nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),    nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),    nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('job_comments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_job_comments_job_num'),    ['job_num'],    unique=False)
        batch_op.create_index(batch_op.f('ix_job_comments_user_id'),    ['user_id'],    unique=False)
        batch_op.create_index(batch_op.f('ix_job_comments_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('job_comments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_comments_created_at'))
        batch_op.drop_index(batch_op.f('ix_job_comments_user_id'))
        batch_op.drop_index(batch_op.f('ix_job_comments_job_num'))
    op.drop_table('job_comments')
