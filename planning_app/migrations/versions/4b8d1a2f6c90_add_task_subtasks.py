"""Add one-level subtasks to project tasks."""

from alembic import op
import sqlalchemy as sa

revision = "4b8d1a2f6c90"
down_revision = "e2a9417c630b"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_tasks") as batch_op:
        batch_op.add_column(sa.Column("parent_task_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_project_tasks_parent_task_id_project_tasks",
            "project_tasks",
            ["parent_task_id"],
            ["id"],
        )
        batch_op.create_index("ix_project_tasks_parent_task_id", ["parent_task_id"])
        batch_op.create_check_constraint(
            "task_subtask_one_parent",
            "parent_task_id IS NULL OR "
            "(project_id IS NULL AND activity_id IS NULL)",
        )


def downgrade():
    with op.batch_alter_table("project_tasks") as batch_op:
        batch_op.drop_constraint("task_subtask_one_parent", type_="check")
        batch_op.drop_index("ix_project_tasks_parent_task_id")
        batch_op.drop_constraint(
            "fk_project_tasks_parent_task_id_project_tasks", type_="foreignkey"
        )
        batch_op.drop_column("parent_task_id")
