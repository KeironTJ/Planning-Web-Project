"""Add persistent manual ordering for projects, activities and tasks."""

from alembic import op
import sqlalchemy as sa

revision = "6f2c9a1d4e80"
down_revision = "4b8d1a2f6c90"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("projects", "project_activities", "project_tasks"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("sort_order", sa.Integer(), nullable=True))
        op.execute(
            sa.text(f"UPDATE {table} SET sort_order = 0 WHERE sort_order IS NULL")
        )
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("sort_order", existing_type=sa.Integer(), nullable=False)


def downgrade():
    for table in ("project_tasks", "project_activities", "projects"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("sort_order")
