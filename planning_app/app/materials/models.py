"""
Materials availability models.

All tables are full-replace on each daily CSV import (truncate + reload).
Date fields from ERP CSVs are Excel serial numbers - converted on import.

MrpExemptMaterial is the exception — it is manually maintained and persists
across imports. Materials on this list are excluded from shortage calculations
and treated as fully covered in SO material status.
"""

from datetime import datetime, timezone

from app.extensions import db


class Stock(db.Model):
    """Current stock on hand per product. Imported from StockOnHand_HIDE.csv (full replace daily)."""

    __tablename__ = "stock"

    id = db.Column(db.Integer, primary_key=True)
    product_code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.String(200), nullable=True)
    qty_on_hand = db.Column(db.Numeric(12, 3), nullable=False, default=0)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<Stock {self.product_code}>"


class PurchaseOrder(db.Model):
    """
    Open inbound purchase order line.
    Imported from OpenPO_HIDE.csv (full replace daily).
    Note: OUSTANDINGQTY is a typo in the ERP export - matched exactly.
    """

    __tablename__ = "purchase_orders"
    __table_args__ = (
        db.UniqueConstraint("po_number", "line_number", name="uq_po_line"),
    )

    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(30), nullable=False, index=True)
    line_number = db.Column(db.Integer, nullable=False)
    product_code = db.Column(db.String(50), nullable=True, index=True)
    description = db.Column(db.String(200), nullable=True)
    outstanding_qty = db.Column(db.Numeric(12, 3), nullable=True)
    due_date = db.Column(db.Date, nullable=True, index=True)
    supplier_code = db.Column(db.String(30), nullable=True)
    supplier_name = db.Column(db.String(100), nullable=True)
    po_type = db.Column(db.String(20), nullable=True)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<PurchaseOrder {self.po_number}/{self.line_number}>"


class MaterialRequirementMain(db.Model):
    """MRP material requirements for main line orders. Imported from MainMaterialReq_HIDE.csv."""

    __tablename__ = "material_requirements_main"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.String(30), nullable=True, index=True)
    batch_id = db.Column(db.String(30), nullable=True)
    works_order = db.Column(db.String(30), nullable=True, index=True)
    load_date = db.Column(db.Date, nullable=True)
    due_date = db.Column(db.Date, nullable=True, index=True)
    department = db.Column(db.String(100), nullable=True, index=True)
    material_code = db.Column(db.String(50), nullable=True, index=True)
    material_description = db.Column(db.String(200), nullable=True)
    qty_required_per_set = db.Column(db.Numeric(12, 3), nullable=True)
    qty_for_order = db.Column(db.Numeric(12, 3), nullable=True)
    qty_issued = db.Column(db.Numeric(12, 3), nullable=True, default=0)
    product_group = db.Column(db.String(30), nullable=True)
    product_group_desc = db.Column(db.String(100), nullable=True)
    complete = db.Column(db.String(1), nullable=True)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<MaterialRequirementMain {self.works_order} {self.material_code}>"


class MaterialRequirementAfterSales(db.Model):
    """MRP material requirements for after sales orders. Imported from ASMaterialReq_HIDE.csv."""

    __tablename__ = "material_requirements_aftersales"

    id = db.Column(db.Integer, primary_key=True)
    customer = db.Column(db.String(100), nullable=True)
    customer_product_ref = db.Column(db.String(50), nullable=True)
    order_number = db.Column(db.String(30), nullable=True, index=True)
    load_date = db.Column(db.Date, nullable=True)
    due_date = db.Column(db.Date, nullable=True, index=True)
    department = db.Column(db.String(100), nullable=True, index=True)
    product_code = db.Column(db.String(50), nullable=True, index=True)
    description = db.Column(db.String(200), nullable=True)
    qty_required = db.Column(db.Numeric(12, 3), nullable=True)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<MaterialRequirementAfterSales {self.order_number} {self.product_code}>"


class MrpExemptMaterial(db.Model):
    """
    Materials excluded from MRP shortage calculations.

    Persists across CSV imports — never truncated. Materials here are not
    managed via the MRP system (no POs raised), so shortage reporting would
    produce false positives. They are still visible in MRP pegging.
    """

    __tablename__ = "mrp_exempt_materials"

    material_code = db.Column(db.String(50), primary_key=True)
    reason = db.Column(db.Text, nullable=True)
    exempted_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    exempted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    exempted_by = db.relationship("User", backref="mrp_exemptions")

    def __repr__(self):
        return f"<MrpExemptMaterial {self.material_code}>"
