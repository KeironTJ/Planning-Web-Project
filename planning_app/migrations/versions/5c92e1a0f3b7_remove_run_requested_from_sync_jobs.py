"""remove_run_requested_from_sync_jobs

Revision ID: 5c92e1a0f3b7
Revises: 4b41db2532de
Create Date: 2026-09-01 22:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5c92e1a0f3b7'
down_revision = '4b41db2532de'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sync_jobs', schema=None) as batch_op:
        batch_op.drop_column('run_requested')


def downgrade():
    with op.batch_alter_table('sync_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('run_requested', sa.Boolean(), nullable=False, server_default=sa.false()))
