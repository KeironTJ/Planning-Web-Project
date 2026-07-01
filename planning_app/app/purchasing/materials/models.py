"""
Materials availability models.

All tables are full-replace on each daily CSV import (truncate + reload).
Date fields from ERP CSVs are Excel serial numbers — converted on import.

All data is scoped to a Site via site_id.

MrpExemptMaterial is the exception — it is manually maintained and persists
across imports. Materials on this list are excluded from shortage calculations
and treated as fully covered in SO material status.
"""

from datetime import datetime, timezone

from app.extensions import db


class Stock(db.Model):
    """
    Stock on hand per part per plant.  Full replace on every Epicor API sync.

    Field names mirror the PlanningStockReport BAQ exactly so that the importer
    mapping is unambiguous and schema changes in Epicor are obvious here.
    """

    __tablename__ = "stock"
    __table_args__ = (
        db.UniqueConstraint("part_num", "plant", name="uq_stock_part_plant"),
    )

    id = db.Column(db.Integer, primary_key=True)

    # --- Identity ---
    part_num          = db.Column(db.String(50),  nullable=False, index=True)   # Part_PartNum
    part_description  = db.Column(db.String(200), nullable=True)                # Part_PartDescription
    class_id          = db.Column(db.String(50),  nullable=True, index=True)    # Part_ClassID
    unit_of_measure   = db.Column(db.String(10),  nullable=True)                # Part_IUM
    plant             = db.Column(db.String(20),  nullable=True, index=True)    # PartPlant_Plant

    # --- Quantities ---
    qty_on_hand              = db.Column(db.Numeric(14, 3), nullable=True)      # Calculated_TotalOnHand2
    qty_on_hand_stores       = db.Column(db.Numeric(14, 3), nullable=True)      # Calculated_TotalOnHandSTORES
    qty_on_hand_prod_uk      = db.Column(db.Numeric(14, 3), nullable=True)      # Calculated_TotalOnHandPRODUK
    qty_on_hand_romania      = db.Column(db.Numeric(14, 3), nullable=True)      # Calculated_TotalOnHandROMANIA
    qty_on_hand_others       = db.Column(db.Numeric(14, 3), nullable=True)      # Calculated_TotalOnHandOTHERS
    qty_required             = db.Column(db.Numeric(14, 3), nullable=True)      # Calculated_TOTALRequiredQty2
    qty_required_unreleased  = db.Column(db.Numeric(14, 3), nullable=True)      # Calculated_TOTALUnRelRequiredQty2
    qty_open_po              = db.Column(db.Numeric(14, 3), nullable=True)      # Calculated_OpenPOQty
    qty_inspection           = db.Column(db.Numeric(14, 3), nullable=True)      # Calculated_InspQty

    # --- MRP position ---
    surplus_deficit             = db.Column(db.Numeric(14, 3), nullable=True)   # Calculated_SurplusDeficitStock
    surplus_deficit_unreleased  = db.Column(db.Numeric(14, 3), nullable=True)   # Calculated_SurplusDeficitStockUR
    insufficient_stock          = db.Column(db.Boolean, nullable=True, index=True)  # Calculated_InsufficientStock

    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<Stock {self.part_num} ({self.plant})>"


class PurchaseOrder(db.Model):
    """
    Open inbound PO release.  Full replace on every Epicor API sync.

    Granularity: one row per PO number + line + release (PORel level).
    Field names mirror the OSPurchaseOrders BAQ exactly.
    """

    __tablename__ = "purchase_orders"
    __table_args__ = (
        db.UniqueConstraint("po_num", "po_line", "po_release", name="uq_po_num_line_rel"),
    )

    id = db.Column(db.Integer, primary_key=True)

    # --- Release identity ---
    po_num      = db.Column(db.Integer,     nullable=False, index=True)   # PORel_PONum
    po_line     = db.Column(db.Integer,     nullable=False)               # PORel_POLine
    po_release  = db.Column(db.Integer,     nullable=False)               # PORel_PORelNum

    # --- Status flags ---
    open_order   = db.Column(db.Boolean, nullable=True)                   # POHeader_OpenOrder
    open_line    = db.Column(db.Boolean, nullable=True)                   # PODetail_OpenLine
    open_release = db.Column(db.Boolean, nullable=True)                   # PORel_OpenRelease

    # --- Dates ---
    order_date   = db.Column(db.Date, nullable=True)                      # POHeader_OrderDate
    due_date     = db.Column(db.Date, nullable=True, index=True)          # PORel_DueDate
    promise_date = db.Column(db.Date, nullable=True)                      # PORel_PromiseDt

    # --- Part / line detail ---
    part_num        = db.Column(db.String(50),  nullable=True, index=True) # PODetail_PartNum
    line_desc       = db.Column(db.String(255), nullable=True)             # PODetail_LineDesc
    unit_of_measure = db.Column(db.String(10),  nullable=True)             # PODetail_PUM

    # --- Quantities ---
    rel_qty         = db.Column(db.Numeric(14, 3), nullable=True)          # PORel_RelQty
    arrived_qty     = db.Column(db.Numeric(14, 3), nullable=True)          # PORel_ArrivedQty
    received_qty    = db.Column(db.Numeric(14, 3), nullable=True)          # PORel_ReceivedQty
    outstanding_qty = db.Column(db.Numeric(14, 3), nullable=True, index=True) # Calculated_OutstandingQty
    invoiced_qty    = db.Column(db.Numeric(14, 3), nullable=True)          # PORel_InvoicedQty

    # --- Pricing ---
    unit_cost      = db.Column(db.Numeric(14, 4), nullable=True)           # PODetail_UnitCost
    doc_unit_cost  = db.Column(db.Numeric(14, 4), nullable=True)           # PODetail_DocUnitCost
    cost_per_code  = db.Column(db.String(5),      nullable=True)           # PODetail_CostPerCode
    currency_code  = db.Column(db.String(10),     nullable=True)           # POHeader_CurrencyCode
    exchange_rate  = db.Column(db.Numeric(12, 6), nullable=True)           # POHeader_ExchangeRate

    # --- Supplier ---
    supplier_id   = db.Column(db.String(20),  nullable=True, index=True)   # Vendor_VendorID
    supplier_name = db.Column(db.String(150), nullable=True)               # Vendor_Name

    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<PurchaseOrder {self.po_num}/{self.po_line}/{self.po_release}>"


class MaterialRequirementMain(db.Model):
    """
    MRP material requirements from PlanningMatReq BAQ.  Full replace daily.

    Granularity: one row per job + assembly + material line + SO number.
    No unique constraint — the BAQ can return the same mtl_seq linked to
    multiple SO lines; we deduplicate in the importer before inserting.
    """

    __tablename__ = "material_requirements"

    id = db.Column(db.Integer, primary_key=True)

    # --- Job header ---
    works_order       = db.Column(db.String(20),  nullable=True, index=True)  # JobHead_JobNum
    job_released      = db.Column(db.Boolean,     nullable=True)
    job_firm          = db.Column(db.Boolean,     nullable=True)
    job_complete      = db.Column(db.Boolean,     nullable=True, index=True)
    job_closed        = db.Column(db.Boolean,     nullable=True, index=True)
    due_date          = db.Column(db.Date,        nullable=True, index=True)  # JobHead_ReqDueDate
    finished_part_num = db.Column(db.String(50),  nullable=True, index=True)  # JobHead_PartNum
    finished_part_desc= db.Column(db.String(200), nullable=True)
    prod_qty          = db.Column(db.Numeric(14, 3), nullable=True)
    plant             = db.Column(db.String(20),  nullable=True, index=True)
    prod_plnwk        = db.Column(db.String(20),  nullable=True)
    model             = db.Column(db.String(100), nullable=True)
    size              = db.Column(db.String(50),  nullable=True)
    so_type           = db.Column(db.String(20),  nullable=True)
    so_number         = db.Column(db.String(20),  nullable=True, index=True)  # OrderHed_OrderNum (str)

    # --- Assembly ---
    assembly_seq  = db.Column(db.Integer,     nullable=True)
    assembly_desc = db.Column(db.String(200), nullable=True)

    # --- Material line ---
    mtl_seq              = db.Column(db.Integer,      nullable=True)
    material_code        = db.Column(db.String(50),   nullable=True, index=True)  # JobMtl_PartNum
    material_description = db.Column(db.String(200),  nullable=True)
    backflush            = db.Column(db.Boolean,      nullable=True)
    qty_per              = db.Column(db.Numeric(14, 4), nullable=True)
    qty_for_order        = db.Column(db.Numeric(14, 3), nullable=True)  # JobMtl_RequiredQty
    qty_issued           = db.Column(db.Numeric(14, 3), nullable=True)  # JobMtl_IssuedQty
    issued_complete      = db.Column(db.Boolean,      nullable=True, index=True)
    related_operation    = db.Column(db.Integer,      nullable=True)
    warehouse_code       = db.Column(db.String(10),   nullable=True)
    class_id             = db.Column(db.String(50),   nullable=True, index=True)

    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<MaterialRequirementMain {self.works_order}/{self.assembly_seq}/{self.mtl_seq}>"


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
