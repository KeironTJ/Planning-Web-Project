"""Relational work items with retained history and root-level access grants."""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy.orm import declared_attr
from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Status(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


def enum_column(enum, name):
    return db.Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda cls: [item.value for item in cls],
    )


class WorkFields:
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    owner_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    department = db.Column(db.String(100), nullable=False)
    status = db.Column(
        enum_column(Status, "work_status"), nullable=False, default=Status.PLANNED
    )
    priority = db.Column(
        enum_column(Priority, "work_priority"), nullable=False, default=Priority.NORMAL
    )
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    deadline = db.Column(db.Date, index=True)
    budget = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    actuals = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    deleted_at = db.Column(db.DateTime(timezone=True))
    version = db.Column(db.Integer, nullable=False, default=1)

    @declared_attr
    def __mapper_args__(cls):
        return {"version_id_col": cls.version}

    @property
    def variance(self):
        return self.budget - self.actuals


def work_constraints(prefix):
    return (
        db.CheckConstraint("budget >= 0 AND actuals >= 0", name=f"{prefix}_money"),
        db.CheckConstraint(
            "start_date IS NULL OR end_date IS NULL OR end_date >= start_date",
            name=f"{prefix}_dates",
        ),
    )


class Project(WorkFields, db.Model):
    __tablename__ = "projects"
    __table_args__ = work_constraints("project")


class Activity(WorkFields, db.Model):
    __tablename__ = "project_activities"
    __table_args__ = work_constraints("activity")
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), index=True)
    project = db.relationship("Project", foreign_keys=[project_id])


class Task(WorkFields, db.Model):
    __tablename__ = "project_tasks"
    __table_args__ = work_constraints("task") + (
        db.CheckConstraint(
            "activity_id IS NULL OR project_id IS NULL", name="task_one_parent"
        ),
        db.CheckConstraint(
            "parent_task_id IS NULL OR "
            "(project_id IS NULL AND activity_id IS NULL)",
            name="task_subtask_one_parent",
        ),
    )
    # An activity's project is derived rather than stored twice.
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), index=True)
    activity_id = db.Column(
        db.Integer, db.ForeignKey("project_activities.id"), index=True
    )
    parent_task_id = db.Column(
        db.Integer, db.ForeignKey("project_tasks.id"), index=True
    )
    project = db.relationship("Project", foreign_keys=[project_id])
    activity = db.relationship("Activity", foreign_keys=[activity_id])
    parent_task = db.relationship(
        "Task", remote_side="Task.id", foreign_keys=[parent_task_id]
    )
    subtasks = db.relationship(
        "Task", foreign_keys=[parent_task_id], back_populates="parent_task"
    )
    assigned_users = db.relationship(
        "User", secondary="project_task_assignees", lazy="selectin"
    )


db.Table(
    "project_task_assignees",
    db.Column(
        "task_id", db.Integer, db.ForeignKey("project_tasks.id"), primary_key=True
    ),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
)


def target_columns():
    return (
        db.Column("project_id", db.Integer, db.ForeignKey("projects.id"), index=True),
        db.Column(
            "activity_id",
            db.Integer,
            db.ForeignKey("project_activities.id"),
            index=True,
        ),
        db.Column("task_id", db.Integer, db.ForeignKey("project_tasks.id"), index=True),
    )


ONE_TARGET = (
    "(CASE WHEN project_id IS NULL THEN 0 ELSE 1 END + "
    "CASE WHEN activity_id IS NULL THEN 0 ELSE 1 END + "
    "CASE WHEN task_id IS NULL THEN 0 ELSE 1 END) = 1"
)


class Share(db.Model):
    __tablename__ = "project_shares"
    __table_args__ = (
        db.CheckConstraint(ONE_TARGET, name="share_one_target"),
        db.CheckConstraint(
            "(user_id IS NULL AND department IS NOT NULL) OR "
            "(user_id IS NOT NULL AND department IS NULL)",
            name="share_one_recipient",
        ),
        db.CheckConstraint("role IN ('viewer', 'editor')", name="share_role"),
        *(
            db.UniqueConstraint(target, recipient, name=f"share_{target}_{recipient}")
            for target in ("project_id", "activity_id", "task_id")
            for recipient in ("user_id", "department")
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    project_id, activity_id, task_id = target_columns()
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    department = db.Column(db.String(100), index=True)
    role = db.Column(db.String(10), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class LogEntry(db.Model):
    __tablename__ = "project_logs"
    __table_args__ = (db.CheckConstraint(ONE_TARGET, name="log_one_target"),)
    id = db.Column(db.Integer, primary_key=True)
    project_id, activity_id, task_id = target_columns()
    body = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="comment")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    deleted_at = db.Column(db.DateTime(timezone=True))
    version = db.Column(db.Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}
