"""
Orders domain models.

Covers the core planning entities:
- Department:       production work centres
- ImportBatch:      audit log of all data imports (CSV and Epicor API)
- SalesOrderComment: free-text comments against sales orders
- SalesOrder:       live sales order data from Epicor API (bskyVA05v1)
"""

from datetime import datetime, date, timezone
from decimal import Decimal
from app.extensions import db


# ---------------------------------------------------------------------------
# Department (Work Centre)
# ---------------------------------------------------------------------------

class Department(db.Model):
    """
    A production department / work centre, scoped to a Site.

    Department codes are unique within a site. Departments are created via
    Admin â†’ Departments; there are no hardcoded defaults.
    """

    __tablename__ = "departments"
    __table_args__ = (
        db.UniqueConstraint("site_id", "code", name="uq_dept_site_code"),
    )

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(
        db.Integer,
        db.ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    code = db.Column(db.String(50), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    op_code = db.Column(db.String(50), nullable=True, index=True)  # Epicor next_op code (e.g. SEW, FRAME)
    target_hours_per_day = db.Column(db.Numeric(5, 2), nullable=True)
    flow_order = db.Column(db.Integer, nullable=True)   # Position in production flow (1 = first); NULL = unset
    track = db.Column(db.Boolean, default=True, nullable=False)  # Include in Daily Output and other tracked views
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    capacity_buckets = db.relationship("CapacityBucket", back_populates="department", cascade="all, delete-orphan")

    @classmethod
    def get_by_name(cls, name: str):
        """Case-insensitive lookup by department name (for CSV import matching)."""
        return cls.query.filter(db.func.lower(cls.name) == name.strip().lower()).first()

    def __repr__(self) -> str:
        return f"<Department {self.name}>"


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------

class ImportBatch(db.Model):
    """
    Audit record for every CSV file upload.

    Created at the start of each import, updated with results on completion.
    """

    __tablename__ = "import_batches"

    # Import type constants
    TYPE_OOB = "oob"                       # legacy open order book (old format)
    TYPE_SALES = "sales"                   # Epicor SALES CSV â†’ SalesOrderLine
    TYPE_COOIS = "coois"                   # Epicor COOIS CSV â†’ WorksOrderOperation
    TYPE_STOCK = "stock"
    TYPE_OPEN_PO = "open_po"
    TYPE_MAIN_MATERIAL = "main_material"
    TYPE_LABOUR_PLAN = "labour_plan"

    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_PARTIAL = "partial"

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(
        db.Integer,
        db.ForeignKey("sites.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    import_type = db.Column(db.String(30), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=True)
    uploaded_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    row_count = db.Column(db.Integer, default=0)
    rows_inserted = db.Column(db.Integer, default=0)
    rows_updated = db.Column(db.Integer, default=0)
    rows_closed = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default=STATUS_PENDING, nullable=False)
    error_message = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])

    def __repr__(self) -> str:
        return f"<ImportBatch {self.import_type} {self.uploaded_at}>"


# ---------------------------------------------------------------------------
# Sales Order Comment
# ---------------------------------------------------------------------------

class SalesOrderComment(db.Model):
    """
    Free-text comment left by a user against a sales order number.
    Append-only â€” comments are never edited or deleted.
    """

    __tablename__ = "so_comments"

    id         = db.Column(db.Integer, primary_key=True)
    so_number  = db.Column(db.String(50), nullable=False, index=True)
    user_id    = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    body       = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    user = db.relationship("User", backref=db.backref("so_comments", lazy="dynamic"))


# ---------------------------------------------------------------------------
# Epicor Sales Order (from bskyVA05v1 BAQ)
# ---------------------------------------------------------------------------

class SalesOrder(db.Model):
    """
    Sales order release line from the bskyVA05v1 BAQ.

    Granularity: one row per OrderNum + OrderLine + OrderRelNum.
    Open orders are fully replaced on every daily sync.
    Closed orders are merged in on-demand by date range.
    """

    __tablename__ = "sales_orders"
    # No unique constraint — the BAQ can return the same order/line/assembly
    # combination from multiple production jobs. Deduplicated in the importer.

    id = db.Column(db.Integer, primary_key=True)

    # --- Order identity ---
    order_num  = db.Column(db.Integer,    nullable=False, index=True)    # OrderHed_OrderNum
    order_line = db.Column(db.Integer,    nullable=False)                 # OrderDtl_OrderLine
    rel_num    = db.Column(db.Integer,    nullable=False)                 # OrderRel_OrderRelNum
    po_num     = db.Column(db.String(50), nullable=True)                  # OrderHed_PONum

    # --- Status flags ---
    open_order   = db.Column(db.Boolean, nullable=True, index=True)       # OrderHed_OpenOrder
    open_line    = db.Column(db.Boolean, nullable=True)                    # OrderDtl_OpenLine
    open_release = db.Column(db.Boolean, nullable=True)                   # OrderRel_OpenRelease
    firm_order   = db.Column(db.Boolean, nullable=True)                   # OrderHed_FirmOrder_c
    firm_line    = db.Column(db.Boolean, nullable=True)                   # OrderDtl_FirmLine_c
    firm_release = db.Column(db.Boolean, nullable=True)                   # OrderRel_FirmRelease
    void_line    = db.Column(db.Boolean, nullable=True)                   # OrderDtl_VoidLine
    order_held   = db.Column(db.Boolean, nullable=True)                   # OrderHed_OrderHeld
    so_credit_hold       = db.Column(db.Boolean, nullable=True)           # Calculated_SOCreditHold
    customer_credit_hold = db.Column(db.Boolean, nullable=True)           # Customer_CreditHold
    guaranteed_christmas = db.Column(db.Boolean, nullable=True)           # OrderHed_GuaranteedChristmasDelivery_c
    display_order        = db.Column(db.Boolean, nullable=True)           # OrderHed_DisplayOrder_c

    # --- Dates ---
    order_date     = db.Column(db.Date, nullable=True, index=True)        # OrderHed_OrderDate
    need_by_date   = db.Column(db.Date, nullable=True, index=True)        # OrderRel_NeedByDate
    req_date       = db.Column(db.Date, nullable=True)                    # OrderRel_ReqDate
    original_ship_by     = db.Column(db.Date, nullable=True)              # OrderRel_OriginalShipBy_c
    original_need_by     = db.Column(db.Date, nullable=True)              # OrderRel_OriginalNeedBy_c
    order_received_date  = db.Column(db.Date, nullable=True)              # OrderHed_OrderReceivedDate_c
    customer_delivery_requested = db.Column(db.Date, nullable=True)       # OrderHed_CustomerDeliveryDateRequested_c
    last_xmas_order_date = db.Column(db.Date, nullable=True)              # Customer_LastOrderReceivedDateGuaranteedChristmas_c
    last_xmas_delivery   = db.Column(db.Date, nullable=True)              # Customer_LastChristmasDeliveryDate_c
    order_ack_sent       = db.Column(db.Date, nullable=True)              # OrderHed_OrderAckSent_c

    # --- Customer ---
    customer_id      = db.Column(db.String(20),  nullable=True, index=True)  # Customer_CustID
    customer_name    = db.Column(db.String(150), nullable=True)               # Customer_Name
    customer_country = db.Column(db.String(100), nullable=True)               # Customer_Country
    customer_group   = db.Column(db.String(50),  nullable=True, index=True)  # Customer_GroupCode
    ship_to_num      = db.Column(db.String(20),  nullable=True)               # OrderHed_ShipToNum
    sales_rep        = db.Column(db.String(50),  nullable=True)               # OrderHed_SalesRepList
    sur_name         = db.Column(db.String(100), nullable=True)               # OrderHed_SurName_c

    # --- Order type / channel ---
    so_type          = db.Column(db.String(20),  nullable=True, index=True)   # OrderHed_SOType_c
    so_type_desc     = db.Column(db.String(100), nullable=True)               # UDCodes_CodeDesc
    channel          = db.Column(db.String(50),  nullable=True, index=True)   # ICCode_Description
    prod_code        = db.Column(db.String(50),  nullable=True, index=True)   # OrderDtl_ProdCode

    # --- Staff ---
    entry_person = db.Column(db.String(50), nullable=True)                    # OrderHed_EntryPerson
    created_by   = db.Column(db.String(50), nullable=True)                    # OrderDtl_CreatedBy_c

    # --- Job linkage ---
    job_num      = db.Column(db.String(20),  nullable=True, index=True)       # JobProd_JobNum
    job_released = db.Column(db.Boolean,     nullable=True)                   # JobHead_JobReleased
    job_firm     = db.Column(db.Boolean,     nullable=True)                   # JobHead_JobFirm
    prod_plnwk   = db.Column(db.String(20),  nullable=True, index=True)       # JobHead_ProdPlnWk_c
    assembly_seq = db.Column(db.Integer,     nullable=True)                   # JobAsmbl_AssemblySeq

    # --- Part / product ---
    part_num      = db.Column(db.String(50),  nullable=True, index=True)      # Calculated_PartNum
    part_desc     = db.Column(db.String(255), nullable=True)                  # Calculated_PartDesc
    base_part_num = db.Column(db.String(50),  nullable=True)                  # OrderDtl_BasePartNum
    xpart_num     = db.Column(db.String(50),  nullable=True)                  # OrderDtl_XPartNum
    ium           = db.Column(db.String(10),  nullable=True)                  # OrderDtl_IUM
    wip_bin       = db.Column(db.String(20),  nullable=True)                  # PartWip_BinNum

    # --- Product configuration ---
    model         = db.Column(db.String(100), nullable=True)                  # Calculated_Model
    size_desc     = db.Column(db.String(100), nullable=True)                  # Calculated_SizeDesc
    prod_size     = db.Column(db.String(100), nullable=True)                  # Calculated_ProdSize
    cover         = db.Column(db.String(50),  nullable=True)                  # Calculated_Cover
    cover_desc    = db.Column(db.String(100), nullable=True)                  # Calculated_CoverDesc
    leg           = db.Column(db.String(100), nullable=True)                  # Calculated_Leg
    leg_mtl       = db.Column(db.String(50),  nullable=True)                  # Calculated_LegMtl
    castor_mtl    = db.Column(db.String(50),  nullable=True)                  # Calculated_CastorMtl
    stud1_mtl     = db.Column(db.String(50),  nullable=True)                  # Calculated_Stud1Mtl
    stud2_mtl     = db.Column(db.String(50),  nullable=True)                  # Calculated_Stud2Mtl
    seat_interior_mtl = db.Column(db.String(50), nullable=True)               # Calculated_SeatInteriorMtl
    back_interior_mtl = db.Column(db.String(50), nullable=True)               # Calculated_BackInteriorMtl
    scat_interior_mtl = db.Column(db.String(50), nullable=True)               # Calculated_ScatInteriorMtl

    # --- Materials (cover codes 1–8 + descriptions) ---
    material_1 = db.Column(db.String(50), nullable=True)                      # Calculated_Material1
    material_1_desc = db.Column(db.String(100), nullable=True)                # Calculated_Cover1PartDesc
    material_2 = db.Column(db.String(50), nullable=True)
    material_2_desc = db.Column(db.String(100), nullable=True)
    material_3 = db.Column(db.String(50), nullable=True)
    material_3_desc = db.Column(db.String(100), nullable=True)
    material_4 = db.Column(db.String(50), nullable=True)
    material_4_desc = db.Column(db.String(100), nullable=True)
    material_5 = db.Column(db.String(50), nullable=True)
    material_5_desc = db.Column(db.String(100), nullable=True)
    material_6 = db.Column(db.String(50), nullable=True)
    material_6_desc = db.Column(db.String(100), nullable=True)
    material_7 = db.Column(db.String(50), nullable=True)
    material_7_desc = db.Column(db.String(100), nullable=True)
    material_8 = db.Column(db.String(50), nullable=True)
    material_8_desc = db.Column(db.String(100), nullable=True)

    # --- Quantities ---
    selling_qty   = db.Column(db.Numeric(12, 3), nullable=True)               # Calculated_SellingQty
    shipped_qty   = db.Column(db.Numeric(12, 3), nullable=True)               # Calculated_ShippedQty
    required_qty  = db.Column(db.Numeric(12, 3), nullable=True)               # JobAsmbl_RequiredQty
    qty_completed = db.Column(db.Numeric(12, 3), nullable=True)               # JobHead_QtyCompleted
    release_qty   = db.Column(db.Numeric(12, 3), nullable=True)               # Calculated_ReleaseQty

    # --- Pricing ---
    release_price     = db.Column(db.Numeric(14, 4), nullable=True)           # Calculated_ReleasePrice
    release_price_gbp = db.Column(db.Numeric(14, 4), nullable=True)           # Calculated_ReleasePriceGBP
    currency_code     = db.Column(db.String(10),     nullable=True)           # OrderHed_CurrencyCode
    exchange_rate     = db.Column(db.Numeric(12, 6), nullable=True)           # OrderHed_ExchangeRate

    # --- Misc ---
    order_book_comments       = db.Column(db.Text, nullable=True)             # OrderHed_OrderBookComments_c
    ship_by_changed_count     = db.Column(db.Integer, nullable=True)          # OrderRel_ShipByChangedCount_c
    need_by_changed_count     = db.Column(db.Integer, nullable=True)          # OrderRel_NeedByChangedCount_c

    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<SalesOrder {self.order_num}/{self.order_line}/{self.rel_num}>"

    def __repr__(self) -> str:
        return f"<SalesOrderComment so={self.so_number} user_id={self.user_id}>"

