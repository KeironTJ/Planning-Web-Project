"""Add projects, optional-parent activities/tasks, sharing and retained logs."""

from alembic import op
import sqlalchemy as sa

revision = "e2a9417c630b"
down_revision = "d7f924bc3810"
branch_labels = None
depends_on = None


def work_columns():
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("department", sa.String(100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "planned",
                "active",
                "blocked",
                "completed",
                "cancelled",
                name="work_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Enum(
                "low",
                "normal",
                "high",
                "urgent",
                name="work_priority",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("deadline", sa.Date()),
        sa.Column("budget", sa.Numeric(14, 2), nullable=False),
        sa.Column("actuals", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def work_checks(prefix):
    return [
        sa.CheckConstraint("budget >= 0 AND actuals >= 0", name=f"{prefix}_money"),
        sa.CheckConstraint(
            "start_date IS NULL OR end_date IS NULL OR end_date >= start_date",
            name=f"{prefix}_dates",
        ),
    ]


def target_columns():
    return [
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
        sa.Column("activity_id", sa.Integer(), sa.ForeignKey("project_activities.id")),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("project_tasks.id")),
    ]


ONE_TARGET = (
    "(CASE WHEN project_id IS NULL THEN 0 ELSE 1 END + "
    "CASE WHEN activity_id IS NULL THEN 0 ELSE 1 END + "
    "CASE WHEN task_id IS NULL THEN 0 ELSE 1 END) = 1"
)


def upgrade():
    op.create_table("projects", *work_columns(), *work_checks("project"))
    op.create_table(
        "project_activities",
        *work_columns(),
        *work_checks("activity"),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
    )
    op.create_table(
        "project_tasks",
        *work_columns(),
        *work_checks("task"),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
        sa.Column("activity_id", sa.Integer(), sa.ForeignKey("project_activities.id")),
        sa.CheckConstraint(
            "activity_id IS NULL OR project_id IS NULL", name="task_one_parent"
        ),
    )
    op.create_table(
        "project_task_assignees",
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("project_tasks.id"), primary_key=True
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
    )
    op.create_table(
        "project_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        *target_columns(),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("department", sa.String(100)),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(ONE_TARGET, name="share_one_target"),
        sa.CheckConstraint(
            "(user_id IS NULL AND department IS NOT NULL) OR "
            "(user_id IS NOT NULL AND department IS NULL)",
            name="share_one_recipient",
        ),
        sa.CheckConstraint("role IN ('viewer', 'editor')", name="share_role"),
        *(
            sa.UniqueConstraint(target, recipient, name=f"share_{target}_{recipient}")
            for target in ("project_id", "activity_id", "task_id")
            for recipient in ("user_id", "department")
        ),
    )
    op.create_table(
        "project_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        *target_columns(),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(ONE_TARGET, name="log_one_target"),
    )
    for table in ("projects", "project_activities", "project_tasks"):
        for field in ("owner_id", "deadline"):
            op.create_index(f"ix_{table}_{field}", table, [field])
    for table in (
        "project_activities",
        "project_tasks",
        "project_shares",
        "project_logs",
    ):
        fields = ["project_id"]
        if table != "project_activities":
            fields.append("activity_id")
        if table in ("project_shares", "project_logs"):
            fields.append("task_id")
        if table == "project_shares":
            fields.extend(["user_id", "department"])
        for field in fields:
            op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade():
    for table in (
        "project_logs",
        "project_shares",
        "project_task_assignees",
        "project_tasks",
        "project_activities",
        "projects",
    ):
        op.drop_table(table)
