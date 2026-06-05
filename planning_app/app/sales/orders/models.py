"""
Orders domain models.

Covers the core planning entities derived from ERP CSV exports:
- Department: the 18 production work centres
- SalesOrderLine: one parent record per SO + line (from OpenOrderBook CSV)
- WorksOrderOperation: one child record per SO + line + work centre (from OOB CSV)
- ImportBatch: audit log of all CSV uploads
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
    target_hours_per_day = db.Column(db.Numeric(5, 2), nullable=True)
    flow_order = db.Column(db.Integer, nullable=True)   # Position in production flow (1 = first); NULL = unset
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    capacity_buckets = db.relationship("CapacityBucket", back_populates="department", cascade="all, delete-orphan")
    works_order_operations = db.relationship("WorksOrderOperation", back_populates="department")

    @classmethod
    def get_by_name(cls, name: str):
        """Case-insensitive lookup by department name (for CSV import matching)."""
        return cls.query.filter(db.func.lower(cls.name) == name.strip().lower()).first()

    def __repr__(self) -> str:
        return f"<Department {self.name}>"


# ---------------------------------------------------------------------------
# Sales Order Line
# ---------------------------------------------------------------------------

class SalesOrderLine(db.Model):
    """
    One record per Sales Order + line number combination.

    Imported from the Epicor Sales CSV export (UPSERT on each import).
    ERP fields are updated on every import. Epicor handles all processing.
    """

    __tablename__ = "sales_order_lines"
    __table_args__ = (
        db.UniqueConstraint("site_id", "so_number", "line_number", name="uq_sol_site_so_line"),
    )


    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(
        db.Integer,
        db.ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ERP fields (updated on every import)
    so_number = db.Column(db.String(20), nullable=False, index=True)        # SOPNO
    line_number = db.Column(db.Integer, nullable=False)                      # ORDITEM
    customer_code = db.Column(db.String(20), nullable=True, index=True)      # CUSTOMER
    customer_name = db.Column(db.String(150), nullable=True)                 # NAME
    customer_order_ref = db.Column(db.String(50), nullable=True)             # CUSTORDREF
    customer_product_ref = db.Column(db.String(50), nullable=True)           # CUSPRODREF
    order_type = db.Column(db.String(30), nullable=True, index=True)         # ORDERTYPE
    product_code = db.Column(db.String(50), nullable=True, index=True)       # PRODCODE
    product_description = db.Column(db.String(200), nullable=True)           # DESCRIPTION
    qty_ordered = db.Column(db.Numeric(10, 2), nullable=True)                # QTY
    order_date = db.Column(db.Date, nullable=True)                           # ORDDATE (converted from Excel serial)
    due_date = db.Column(db.Date, nullable=True, index=True)                 # DUEDATE (converted from Excel serial)
    unit_price = db.Column(db.Numeric(10, 2), nullable=True)                 # SELLPRICE
    total_value = db.Column(db.Numeric(12, 2), nullable=True)                # TOTALVALUE
    # Open/closed flag — False means line has shipped/closed (ERP Open Line = FALSE)
    is_open = db.Column(db.Boolean, nullable=True, index=True, default=True) # Open Line

    # Product / order context (populated from Sales CSV)
    model = db.Column(db.String(100), nullable=True, index=True)             # Model
    product_size = db.Column(db.String(50), nullable=True)                   # ProdSize
    product_group = db.Column(db.String(50), nullable=True, index=True)      # Product Group
    customer_group = db.Column(db.String(50), nullable=True, index=True)     # Customer Group
    channel = db.Column(db.String(50), nullable=True)                        # IC Description (Home/Export/etc.)
    country = db.Column(db.String(100), nullable=True)                       # Country
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    operations = db.relationship(
        "WorksOrderOperation",
        back_populates="sales_order_line",
        cascade="all, delete-orphan",
        order_by="WorksOrderOperation.work_centre_name",
    )

    @property
    def is_overdue(self) -> bool:
        return (
            self.due_date is not None
            and self.due_date < date.today()
        )

    def __repr__(self) -> str:
        return f"<SalesOrderLine {self.so_number}/{self.line_number}>"


# ---------------------------------------------------------------------------
# Works Order Operation
# ---------------------------------------------------------------------------

class WorksOrderOperation(db.Model):
    """
    Child record â€” one per Sales Order + line + work centre.

    These are the rows from OpenOrderBook_HIDE.csv.
    The ERP has already broken each order down by work centre â€” these ARE the
    works order operations.

    ERP fields are updated on every OOB import.
    Planner fields (planned_date, status, completed_date, notes) are NEVER
    overwritten by the importer.
    """

    __tablename__ = "works_order_operations"
    __table_args__ = (
        db.UniqueConstraint(
            "so_number", "line_number", "work_centre_name",
            name="uq_woo_key",
        ),
    )

    # Status constants
    STATUS_NEW_ORDER   = "new_order"
    STATUS_FIRM_PLANNED = "firm_planned"
    STATUS_RELEASED    = "released"
    STATUS_WIP         = "wip"
    STATUS_COMPLETED   = "completed"
    STATUS_CLOSED      = "closed"


    VALID_STATUSES = [
        STATUS_NEW_ORDER,
        STATUS_FIRM_PLANNED,
        STATUS_RELEASED,
        STATUS_WIP,
        STATUS_COMPLETED,
        STATUS_CLOSED,
    ]

    # Status display labels and Bootstrap badge colours
    STATUS_META = {
        STATUS_NEW_ORDER:    ("New Order",    "secondary"),  # "light" / "dark" are theme-blind; use adaptive colours
        STATUS_FIRM_PLANNED: ("Firm Planned", "info"),
        STATUS_RELEASED:     ("Released",     "primary"),
        STATUS_WIP:          ("WIP",          "warning"),
        STATUS_COMPLETED:    ("Completed",    "success"),
        STATUS_CLOSED:       ("Closed",       "secondary"),  # "dark" blends into dark-mode body bg
    }

    id = db.Column(db.Integer, primary_key=True)
    sales_order_line_id = db.Column(
        db.Integer,
        db.ForeignKey("sales_order_lines.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Denormalised keys for fast upsert lookup without join
    so_number = db.Column(db.String(20), nullable=False, index=True)         # SOPNO
    line_number = db.Column(db.Integer, nullable=False)                       # ORDITEM
    work_centre_name = db.Column(db.String(100), nullable=False, index=True)  # WORKCENTRE

    # ERP fields (updated on every import)
    qty = db.Column(db.Numeric(10, 2), nullable=True)                         # QTY
    due_date = db.Column(db.Date, nullable=True, index=True)                  # DUEDATE
    total_value = db.Column(db.Numeric(12, 2), nullable=True)                 # TOTALVALUE
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Planner fields (NEVER overwritten by importer)
    status = db.Column(db.String(20), default=STATUS_NEW_ORDER, nullable=False, index=True)
    planned_date = db.Column(db.Date, nullable=True, index=True)
    completed_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    sales_order_line = db.relationship("SalesOrderLine", back_populates="operations")
    department = db.relationship("Department", back_populates="works_order_operations")

    @property
    def status_label(self) -> str:
        return self.STATUS_META.get(self.status, ("Unknown", "secondary"))[0]

    @property
    def status_colour(self) -> str:
        return self.STATUS_META.get(self.status, ("Unknown", "secondary"))[1]

    @property
    def is_overdue(self) -> bool:
        return (
            self.due_date is not None
            and self.due_date < date.today()
            and self.status not in (self.STATUS_COMPLETED, self.STATUS_CLOSED)
        )

    def __repr__(self) -> str:
        return f"<WorksOrderOperation {self.so_number}/{self.line_number} {self.work_centre_name}>"
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

    def __repr__(self) -> str:
        return f"<SalesOrderComment so={self.so_number} user_id={self.user_id}>"

