"""
Capacity planning models.

CapacityBucket represents available hours for a Department on a given day.
Populated by importing LabourPlan_HIDE.csv — do not enter manually.

RoutingTemplate / RoutingStage / RoutingStageEntry drive backward scheduling:
- A RoutingTemplate is a named sequence of production stages.
- A RoutingStage groups one or more departments that work in parallel.
  Stages with the same sequence_order run simultaneously; lower = earlier in flow.
- RoutingStageEntry links a department to a stage with an optional LT override.
"""

from app.extensions import db


class CapacityBucket(db.Model):
    """
    Available capacity for a Department on a specific calendar day.

    One row per department per day. Populated via CSV import (LabourPlan_HIDE.csv)
    or edited directly through Admin → Labour Plan.
    The `manually_overridden` flag is set True when a planner edits a
    bucket directly, so re-imports do not overwrite manual adjustments.
    """

    __tablename__ = "capacity_buckets"
    __table_args__ = (
        db.UniqueConstraint("department_id", "date", name="uq_bucket_dept_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date = db.Column(db.Date, nullable=False, index=True)
    week = db.Column(db.String(10), nullable=True, index=True)
    is_workday = db.Column(db.Boolean, default=True, nullable=False)
    day_complete = db.Column(db.Boolean, default=False, nullable=False)
    available_hours = db.Column(db.Numeric(6, 2), nullable=True)
    manually_overridden = db.Column(db.Boolean, default=False, nullable=False)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    department = db.relationship("Department", back_populates="capacity_buckets")

    def __repr__(self) -> str:
        return f"<CapacityBucket dept={self.department_id} {self.date}>"


# ---------------------------------------------------------------------------
# Routing Template
# ---------------------------------------------------------------------------

class RoutingTemplate(db.Model):
    """
    A named production routing, scoped to a Site.

    Defines the sequence and parallelism of department stages for backward
    scheduling. Only one template per site should have is_default=True.
    """

    __tablename__ = "routing_templates"

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(
        db.Integer,
        db.ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    stages = db.relationship(
        "RoutingStage",
        back_populates="template",
        order_by="RoutingStage.sequence_order",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<RoutingTemplate {self.name}>"


class RoutingStage(db.Model):
    """
    A stage within a RoutingTemplate.

    Departments assigned to the same stage (same sequence_order) run in
    parallel — the scheduler uses the maximum LT within the stage as the
    blocking duration before the next stage can begin.
    """

    __tablename__ = "routing_stages"

    id = db.Column(db.Integer, primary_key=True)
    routing_template_id = db.Column(
        db.Integer,
        db.ForeignKey("routing_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(100), nullable=True)      # optional display label
    sequence_order = db.Column(db.Integer, nullable=False)

    template = db.relationship("RoutingTemplate", back_populates="stages")
    entries = db.relationship(
        "RoutingStageEntry",
        back_populates="stage",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<RoutingStage seq={self.sequence_order} '{self.name}'>"


class RoutingStageEntry(db.Model):
    """
    A department assigned to a RoutingStage.

    lead_time_override_days — if set, used instead of the department's
    default_lead_time_days for backward scheduling.
    """

    __tablename__ = "routing_stage_entries"
    __table_args__ = (
        db.UniqueConstraint(
            "routing_stage_id", "department_id", name="uq_rse_stage_dept"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    routing_stage_id = db.Column(
        db.Integer,
        db.ForeignKey("routing_stages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lead_time_override_days = db.Column(db.Integer, nullable=True)  # None = use dept default

    stage = db.relationship("RoutingStage", back_populates="entries")
    department = db.relationship("Department")

    @property
    def effective_lead_time(self) -> int:
        """Return the lead time days to use for scheduling."""
        if self.lead_time_override_days is not None:
            return self.lead_time_override_days
        return self.department.default_lead_time_days if self.department else 2

    def __repr__(self) -> str:
        return f"<RoutingStageEntry stage={self.routing_stage_id} dept={self.department_id}>"
