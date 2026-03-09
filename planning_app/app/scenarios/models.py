"""
Scenario modelling models.

A Scenario is a named "what-if" plan that holds a snapshot of planning
parameters. Planners can compare scenarios against the baseline without
affecting live production data.
"""

from datetime import datetime, timezone
from app.extensions import db


class Scenario(db.Model):
    """A named planning scenario (what-if analysis)."""

    __tablename__ = "scenarios"

    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_ARCHIVED = "archived"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default=STATUS_DRAFT, nullable=False, index=True)
    is_baseline = db.Column(db.Boolean, default=False)  # Only one baseline at a time
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    assumptions = db.relationship("ScenarioAssumption", back_populates="scenario", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Scenario {self.name}>"


class ScenarioAssumption(db.Model):
    """
    A key-value assumption within a scenario.

    Examples:
        key="demand_uplift_pct"  value="15"   (15% demand increase)
        key="capacity_reduction" value="20"   (20% capacity loss)
    """

    __tablename__ = "scenario_assumptions"

    id = db.Column(db.Integer, primary_key=True)
    scenario_id = db.Column(db.Integer, db.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False)
    key = db.Column(db.String(100), nullable=False)
    value = db.Column(db.String(255))
    description = db.Column(db.String(255))

    scenario = db.relationship("Scenario", back_populates="assumptions")

    def __repr__(self) -> str:
        return f"<ScenarioAssumption {self.key}={self.value}>"
