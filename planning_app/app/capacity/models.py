"""
Capacity planning models.

CapacityBucket represents available hours for a Department on a given day.
Populated by importing LabourPlan_HIDE.csv — do not enter manually.
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
