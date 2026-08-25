"""
Planning sandbox models.

PlanningSession       – a named what-if workspace.
PlanningOverride      – per-job date/week changes within a session.
PlanningCapacityTarget – weekly unit-based capacity target (target_per_day × working_days).
CustomerGroup         – named customer group for capacity breakdown (e.g. JL Direct, SOHO).
CustomerGroupMember   – maps an Epicor customer_id to a CustomerGroup (1 customer → 1 group).
"""

from datetime import datetime, timezone

from app.extensions import db


class PlanningSession(db.Model):
    """A named planning sandbox."""

    __tablename__ = "planning_sessions"

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(100), nullable=False)
    description   = db.Column(db.Text, nullable=True)
    is_active     = db.Column(db.Boolean, default=True, nullable=False)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    overrides  = db.relationship(
        "PlanningOverride", back_populates="session", cascade="all, delete-orphan"
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def override_count(self) -> int:
        return len(self.overrides)

    def __repr__(self) -> str:
        return f"<PlanningSession {self.id} '{self.name}'>"


class PlanningOverride(db.Model):
    """Per-job planning override within a PlanningSession."""

    __tablename__ = "planning_overrides"
    __table_args__ = (
        db.UniqueConstraint("session_id", "job_num", "assembly_seq",
                            name="uq_override_session_job"),
    )

    id           = db.Column(db.Integer, primary_key=True)
    session_id   = db.Column(
        db.Integer, db.ForeignKey("planning_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    job_num      = db.Column(db.String(20), nullable=False, index=True)
    assembly_seq = db.Column(db.Integer, nullable=False, default=0)

    override_plnwk    = db.Column(db.String(20), nullable=True)
    override_due_date = db.Column(db.Date, nullable=True)
    notes             = db.Column(db.Text, nullable=True)
    created_at        = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    session    = db.relationship("PlanningSession", back_populates="overrides")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<PlanningOverride session={self.session_id} job={self.job_num}/{self.assembly_seq}>"


# ---------------------------------------------------------------------------
# Capacity volume target
# ---------------------------------------------------------------------------

class PlanningCapacityTarget(db.Model):
    """
    Unit-based weekly capacity target.

    One row per ISO week (e.g. '2026-W35') for week-specific overrides,
    PLUS one row where week IS NULL which acts as the rolling default.

    weekly_capacity = target_per_day × working_days
    """

    __tablename__ = "planning_capacity_targets"
    __table_args__ = (
        db.UniqueConstraint("week", name="uq_cap_target_week"),
    )

    id             = db.Column(db.Integer, primary_key=True)
    week           = db.Column(db.String(10), nullable=True, index=True)  # NULL = default
    target_per_day = db.Column(db.Numeric(8, 2), nullable=False, default=0)
    working_days   = db.Column(db.Integer, nullable=False, default=4)     # Mon–Thu
    max_per_day    = db.Column(db.Numeric(8, 2), nullable=True)
    notes          = db.Column(db.Text, nullable=True)
    updated_at     = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_by_id  = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    @property
    def weekly_target(self) -> float:
        return float(self.target_per_day or 0) * int(self.working_days or 0)

    @property
    def weekly_max(self) -> float:
        if self.max_per_day:
            return float(self.max_per_day) * int(self.working_days or 0)
        return self.weekly_target

    def __repr__(self) -> str:
        return f"<PlanningCapacityTarget week={self.week or 'DEFAULT'} target={self.target_per_day}×{self.working_days}>"


# ---------------------------------------------------------------------------
# Customer groups
# ---------------------------------------------------------------------------

class CustomerGroup(db.Model):
    """
    A named group of Epicor customers for capacity breakdown.

    Examples: 'JL Direct', 'SOHO', 'Barker M&S', 'LOAF'.
    Each active group gets its own column set on the planning board.
    """

    __tablename__ = "customer_groups"
    __table_args__ = (
        db.UniqueConstraint("name", name="uq_cg_name"),
    )

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    colour     = db.Column(db.String(20), nullable=True)   # CSS hex or named colour
    sort_order = db.Column(db.Integer, nullable=True)      # display order on board
    is_active  = db.Column(db.Boolean, default=True, nullable=False)
    weekly_capacity = db.Column(db.Numeric(8, 1), nullable=True)  # planned capacity for this group (units/week)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    members = db.relationship(
        "CustomerGroupMember", back_populates="group", cascade="all, delete-orphan",
        order_by="CustomerGroupMember.customer_id",
    )

    @property
    def member_count(self) -> int:
        return len(self.members)

    def __repr__(self) -> str:
        return f"<CustomerGroup {self.id} '{self.name}'>"


class CustomerGroupMember(db.Model):
    """
    Maps an Epicor customer_id string to exactly one CustomerGroup.

    A customer may only belong to one group at a time.
    """

    __tablename__ = "customer_group_members"
    __table_args__ = (
        db.UniqueConstraint("customer_id", name="uq_cgm_customer_id"),
    )

    id          = db.Column(db.Integer, primary_key=True)
    group_id    = db.Column(
        db.Integer, db.ForeignKey("customer_groups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    customer_id = db.Column(db.String(20), nullable=False, index=True)

    group = db.relationship("CustomerGroup", back_populates="members")

    def __repr__(self) -> str:
        return f"<CustomerGroupMember group={self.group_id} cust={self.customer_id}>"


class CustomerGroupCapacityTarget(db.Model):
    """
    Per-week capacity override for a CustomerGroup.

    week = ISO week label (e.g. '2026-W35').  NOT NULL — always week-specific.
    The group default is stored on CustomerGroup.weekly_capacity.
    Lookup order: week-specific row → CustomerGroup.weekly_capacity → None.
    """

    __tablename__ = "customer_group_capacity_targets"
    __table_args__ = (
        db.UniqueConstraint("group_id", "week", name="uq_cgct_group_week"),
    )

    id               = db.Column(db.Integer, primary_key=True)
    group_id         = db.Column(
        db.Integer, db.ForeignKey("customer_groups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    week             = db.Column(db.String(10), nullable=False, index=True)
    weekly_capacity  = db.Column(db.Numeric(8, 1), nullable=False)

    group = db.relationship(
        "CustomerGroup",
        backref=db.backref("week_capacity_targets", cascade="all, delete-orphan"),
    )

    def __repr__(self) -> str:
        return f"<CustomerGroupCapacityTarget group={self.group_id} {self.week}={self.weekly_capacity}>"

