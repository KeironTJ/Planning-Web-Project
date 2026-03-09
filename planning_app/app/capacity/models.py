"""
Capacity planning data models.

Key concepts:
- WorkCentre: A physical or virtual production resource (machine, cell, team).
- CapacityBucket: Time-boxed available capacity for a WorkCentre (week/day).
- Routing: Sequence of operations required to manufacture a product.
- Operation: One step in a Routing, linked to a WorkCentre with a run time.
- WorkOrder: A manufacturing order demanding capacity from WorkCentres.
- BOM (Bill of Materials): Hierarchical list of components for a product.
"""

from datetime import datetime, timezone
from decimal import Decimal
from app.extensions import db


class WorkCentre(db.Model):
    """
    A production resource capable of performing work.

    Examples: CNC Machine #3, Upholstery Team A, Assembly Line 2.
    """

    __tablename__ = "work_centres"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(100), index=True)
    description = db.Column(db.Text)
    # Available hours per standard shift
    hours_per_shift = db.Column(db.Numeric(5, 2), default=Decimal("8.00"))
    shifts_per_day = db.Column(db.Integer, default=1)
    efficiency_pct = db.Column(db.Numeric(5, 2), default=Decimal("85.00"))  # % utilisation target
    is_active = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    capacity_buckets = db.relationship("CapacityBucket", back_populates="work_centre", cascade="all, delete-orphan")
    operations = db.relationship("Operation", back_populates="work_centre")

    @property
    def daily_capacity_hours(self) -> Decimal:
        """Effective available hours per day accounting for efficiency."""
        return (
            Decimal(str(self.hours_per_shift))
            * Decimal(str(self.shifts_per_day))
            * (Decimal(str(self.efficiency_pct)) / 100)
        )

    def __repr__(self) -> str:
        return f"<WorkCentre {self.code}>"


class CapacityBucket(db.Model):
    """
    Available capacity for a WorkCentre within a specific time period.

    One bucket = one week (or day) of capacity.  Buckets are pre-generated
    by the planning horizon tool and can be manually adjusted.
    """

    __tablename__ = "capacity_buckets"
    __table_args__ = (
        db.UniqueConstraint("work_centre_id", "period_start", name="uq_bucket_wc_period"),
        db.Index("ix_bucket_period", "period_start", "period_end"),
    )

    id = db.Column(db.Integer, primary_key=True)
    work_centre_id = db.Column(db.Integer, db.ForeignKey("work_centres.id", ondelete="CASCADE"), nullable=False)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    available_hours = db.Column(db.Numeric(8, 2), nullable=False)    # Total hours in period
    allocated_hours = db.Column(db.Numeric(8, 2), default=Decimal("0.00"))  # Hours assigned to orders
    notes = db.Column(db.Text)

    work_centre = db.relationship("WorkCentre", back_populates="capacity_buckets")

    @property
    def remaining_hours(self) -> Decimal:
        return Decimal(str(self.available_hours)) - Decimal(str(self.allocated_hours))

    @property
    def utilisation_pct(self) -> float:
        if not self.available_hours:
            return 0.0
        return float(Decimal(str(self.allocated_hours)) / Decimal(str(self.available_hours)) * 100)

    def __repr__(self) -> str:
        return f"<CapacityBucket wc={self.work_centre_id} {self.period_start}>"


class Routing(db.Model):
    """
    A sequence of manufacturing operations for a product/SKU.

    A routing defines HOW something is made (the process path).
    """

    __tablename__ = "routings"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    product_code = db.Column(db.String(50), index=True)   # FK to ERP product (external)
    revision = db.Column(db.String(10), default="1")
    is_active = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    operations = db.relationship(
        "Operation", back_populates="routing",
        order_by="Operation.sequence_no",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Routing {self.code}>"


class Operation(db.Model):
    """
    A single step in a Routing, performed at a WorkCentre.

    Run time is per unit; setup time is fixed per batch.
    """

    __tablename__ = "operations"
    __table_args__ = (
        db.UniqueConstraint("routing_id", "sequence_no", name="uq_op_routing_seq"),
    )

    id = db.Column(db.Integer, primary_key=True)
    routing_id = db.Column(db.Integer, db.ForeignKey("routings.id", ondelete="CASCADE"), nullable=False)
    work_centre_id = db.Column(db.Integer, db.ForeignKey("work_centres.id"), nullable=False)
    sequence_no = db.Column(db.Integer, nullable=False)      # e.g. 10, 20, 30
    description = db.Column(db.String(200))
    setup_time_hrs = db.Column(db.Numeric(6, 3), default=Decimal("0.00"))   # Fixed per batch
    run_time_hrs = db.Column(db.Numeric(6, 4), nullable=False)               # Per unit

    routing = db.relationship("Routing", back_populates="operations")
    work_centre = db.relationship("WorkCentre", back_populates="operations")

    def total_time_hrs(self, quantity: Decimal) -> Decimal:
        """Calculate total operation time for a given quantity."""
        return Decimal(str(self.setup_time_hrs)) + (Decimal(str(self.run_time_hrs)) * quantity)

    def __repr__(self) -> str:
        return f"<Operation {self.routing_id}:{self.sequence_no}>"


class WorkOrder(db.Model):
    """
    A manufacturing order that demands capacity.

    Statuses:
        draft      — not yet released to production
        released   — active in production
        in_progress — work has started
        completed  — fully manufactured
        cancelled  — abandoned
    """

    __tablename__ = "work_orders"

    STATUS_DRAFT = "draft"
    STATUS_RELEASED = "released"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    VALID_STATUSES = [STATUS_DRAFT, STATUS_RELEASED, STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_CANCELLED]

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    product_code = db.Column(db.String(50), nullable=False, index=True)
    product_description = db.Column(db.String(200))
    routing_id = db.Column(db.Integer, db.ForeignKey("routings.id"))
    quantity = db.Column(db.Numeric(10, 2), nullable=False)
    quantity_completed = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    status = db.Column(db.String(20), default=STATUS_DRAFT, nullable=False, index=True)
    priority = db.Column(db.Integer, default=50)       # 1 (highest) — 100 (lowest)
    planned_start = db.Column(db.Date, index=True)
    planned_end = db.Column(db.Date, index=True)
    actual_start = db.Column(db.Date)
    actual_end = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    routing = db.relationship("Routing")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def completion_pct(self) -> float:
        if not self.quantity:
            return 0.0
        return float(Decimal(str(self.quantity_completed)) / Decimal(str(self.quantity)) * 100)

    @property
    def is_overdue(self) -> bool:
        from datetime import date
        return self.planned_end and self.planned_end < date.today() and self.status not in (
            self.STATUS_COMPLETED, self.STATUS_CANCELLED
        )

    def __repr__(self) -> str:
        return f"<WorkOrder {self.order_number}>"


class BOM(db.Model):
    """
    Bill of Materials — hierarchical component list for a product.
    """

    __tablename__ = "boms"

    id = db.Column(db.Integer, primary_key=True)
    parent_product_code = db.Column(db.String(50), nullable=False, index=True)
    component_product_code = db.Column(db.String(50), nullable=False, index=True)
    quantity_per = db.Column(db.Numeric(10, 4), nullable=False)   # Units of component per parent unit
    unit_of_measure = db.Column(db.String(10), default="EA")
    scrap_pct = db.Column(db.Numeric(5, 2), default=Decimal("0.00"))
    is_active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)

    def __repr__(self) -> str:
        return f"<BOM {self.parent_product_code} -> {self.component_product_code}>"
