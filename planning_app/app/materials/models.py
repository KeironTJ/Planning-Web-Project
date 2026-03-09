"""
Material availability models.

Tracks stock levels, supplier lead times, and purchase orders so the
planner can see whether materials are available to support work orders.
"""

from datetime import datetime, timezone
from decimal import Decimal
from app.extensions import db


class Material(db.Model):
    """A purchased or manufactured material/component."""

    __tablename__ = "materials"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.String(200), nullable=False)
    unit_of_measure = db.Column(db.String(10), default="EA")
    stock_on_hand = db.Column(db.Numeric(12, 3), default=Decimal("0.000"))
    safety_stock = db.Column(db.Numeric(12, 3), default=Decimal("0.000"))
    reorder_point = db.Column(db.Numeric(12, 3), default=Decimal("0.000"))
    lead_time_days = db.Column(db.Integer, default=0)
    unit_cost = db.Column(db.Numeric(10, 4), default=Decimal("0.0000"))
    supplier_code = db.Column(db.String(50), index=True)
    is_active = db.Column(db.Boolean, default=True, index=True)
    last_updated = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    purchase_orders = db.relationship("PurchaseOrder", back_populates="material", cascade="all, delete-orphan")

    @property
    def is_below_safety_stock(self) -> bool:
        return Decimal(str(self.stock_on_hand)) < Decimal(str(self.safety_stock))

    @property
    def is_below_reorder_point(self) -> bool:
        return Decimal(str(self.stock_on_hand)) < Decimal(str(self.reorder_point))

    def __repr__(self) -> str:
        return f"<Material {self.code}>"


class PurchaseOrder(db.Model):
    """An inbound purchase order for a material."""

    __tablename__ = "purchase_orders"

    STATUS_OPEN = "open"
    STATUS_RECEIVED = "received"
    STATUS_CANCELLED = "cancelled"

    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    material_id = db.Column(db.Integer, db.ForeignKey("materials.id", ondelete="CASCADE"), nullable=False)
    quantity_ordered = db.Column(db.Numeric(12, 3), nullable=False)
    quantity_received = db.Column(db.Numeric(12, 3), default=Decimal("0.000"))
    expected_date = db.Column(db.Date, index=True)
    received_date = db.Column(db.Date)
    status = db.Column(db.String(20), default=STATUS_OPEN, nullable=False, index=True)
    supplier_code = db.Column(db.String(50))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    material = db.relationship("Material", back_populates="purchase_orders")

    @property
    def quantity_outstanding(self) -> Decimal:
        return Decimal(str(self.quantity_ordered)) - Decimal(str(self.quantity_received))

    def __repr__(self) -> str:
        return f"<PurchaseOrder {self.po_number}>"
