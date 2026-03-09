"""
Workload balancing models.

Tracks operator/team assignments and shift-level workload to support
labour balancing across planning horizons.
"""

from datetime import datetime, timezone
from decimal import Decimal
from app.extensions import db


class Team(db.Model):
    """A group of workers that can be assigned to production tasks."""

    __tablename__ = "teams"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(100))
    headcount = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)

    assignments = db.relationship("WorkloadAssignment", back_populates="team", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Team {self.code}>"


class WorkloadAssignment(db.Model):
    """
    An assignment of a Team to a Work Order for a specific date.

    Planned hours represent the labour expected; actual hours are filled
    in after the shift for variance tracking.
    """

    __tablename__ = "workload_assignments"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    work_order_id = db.Column(db.Integer, db.ForeignKey("work_orders.id", ondelete="SET NULL"))
    work_centre_id = db.Column(db.Integer, db.ForeignKey("work_centres.id", ondelete="SET NULL"))
    assignment_date = db.Column(db.Date, nullable=False, index=True)
    planned_hours = db.Column(db.Numeric(6, 2), nullable=False)
    actual_hours = db.Column(db.Numeric(6, 2))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    team = db.relationship("Team", back_populates="assignments")

    @property
    def variance_hours(self):
        if self.actual_hours is None:
            return None
        return Decimal(str(self.actual_hours)) - Decimal(str(self.planned_hours))

    def __repr__(self) -> str:
        return f"<WorkloadAssignment team={self.team_id} date={self.assignment_date}>"
