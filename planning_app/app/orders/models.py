"""
Orders domain models.

Covers the core planning entities derived from ERP CSV exports:
- Department: the 18 production work centres
- SalesOrderLine: one parent record per SO + line (from OpenOrderBook CSV)
- WorksOrderOperation: one child record per SO + line + work centre (from OOB CSV)
- SmvMatrix: standard minute values per product per department (from SMVTable CSV)
- ProductionFlow: routing flow + lead times per stage (from ProductionFlowLT CSV)
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
    A production department / work centre.

    The 18 confirmed departments are seeded via `python manage.py seed-departments`.
    Each department maps 1:1 to a work centre in the ERP OOB.
    """

    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    target_hours_per_day = db.Column(db.Numeric(5, 2), nullable=True)
    default_lead_time_days = db.Column(db.Integer, default=2, nullable=False, server_default="2")
    flow_order = db.Column(db.Integer, nullable=True)   # Position in production flow (1 = first); NULL = unset
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    capacity_buckets = db.relationship("CapacityBucket", back_populates="department", cascade="all, delete-orphan")
    works_order_operations = db.relationship("WorksOrderOperation", back_populates="department")
    smv_entries = db.relationship("SmvMatrix", back_populates="department", cascade="all, delete-orphan")

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
    Parent record — one per Sales Order + line number combination.

    Imported from OpenOrderBook_HIDE.csv (UPSERT on each daily import).
    ERP fields are updated on every import; planner fields are never touched
    by the importer.
    """

    __tablename__ = "sales_order_lines"
    __table_args__ = (
        db.UniqueConstraint("so_number", "line_number", name="uq_sol_so_line"),
    )

    # ── Line-level aggregate status constants ─────────────────────────── #
    LINE_STATUS_NEW          = "new_order"
    LINE_STATUS_FIRM_PLANNED = "firm_planned"
    LINE_STATUS_WIP          = "wip"
    LINE_STATUS_COMPLETE     = "complete"

    LINE_STATUS_META = {
        "new_order":    ("New Order",    "secondary"),
        "firm_planned": ("Firm Planned", "info"),
        "wip":          ("WIP",          "warning"),
        "complete":     ("Complete",     "success"),
    }

    id = db.Column(db.Integer, primary_key=True)

    # ERP fields (updated on every import)
    so_number = db.Column(db.String(20), nullable=False, index=True)        # SOPNO
    line_number = db.Column(db.Integer, nullable=False)                      # ORDITEM
    customer_code = db.Column(db.String(20), nullable=True, index=True)      # CUSTOMER
    customer_name = db.Column(db.String(150), nullable=True)                 # NAME
    customer_order_ref = db.Column(db.String(50), nullable=True)             # CUSTORDREF
    customer_product_ref = db.Column(db.String(50), nullable=True)           # CUSPRODREF
    order_type = db.Column(db.String(30), nullable=True, index=True)         # ORDERTYPE (e.g. MAINLINE)
    caravan_code = db.Column(db.String(30), nullable=True, index=True)       # CARAVANCODE (product programme)
    caravan_description = db.Column(db.String(200), nullable=True)           # CARAVANDESCRIPTION
    product_code = db.Column(db.String(50), nullable=True, index=True)       # PRODCODE
    product_description = db.Column(db.String(200), nullable=True)           # DESCRIPTION
    qty_ordered = db.Column(db.Numeric(10, 2), nullable=True)                # QTY
    order_date = db.Column(db.Date, nullable=True)                           # ORDDATE (converted from Excel serial)
    due_date = db.Column(db.Date, nullable=True, index=True)                 # DUEDATE (converted from Excel serial)
    unit_price = db.Column(db.Numeric(10, 2), nullable=True)                 # SELLPRICE
    total_value = db.Column(db.Numeric(12, 2), nullable=True)                # TOTALVALUE
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Planner field — set by scheduler for lines that have no operations
    planned_date = db.Column(db.Date, nullable=True, index=True)

    operations = db.relationship(
        "WorksOrderOperation",
        back_populates="sales_order_line",
        cascade="all, delete-orphan",
        order_by="WorksOrderOperation.work_centre_name",
    )

    @property
    def aggregate_status(self) -> str:
        """
        Derive a line-level status from the collection of operations.

        Priority (highest wins):
          complete     — all operations are completed or closed
          wip          — any operation is started, wip, or completed
                         (production has begun on at least one department)
          firm_planned — any open operation has a planned_date set
          new_order    — no planned dates, all operations not_started
        """
        ops = self.operations
        if not ops:
            return self.LINE_STATUS_FIRM_PLANNED if self.planned_date else self.LINE_STATUS_NEW

        open_ops = [op for op in ops if op.status != WorksOrderOperation.STATUS_CLOSED]

        # All closed (shipped / cancelled in ERP)
        if not open_ops:
            return self.LINE_STATUS_COMPLETE

        open_statuses = {op.status for op in open_ops}

        # All remaining open operations are completed
        if open_statuses == {WorksOrderOperation.STATUS_COMPLETED}:
            return self.LINE_STATUS_COMPLETE

        # Any production activity → WIP
        production_statuses = {
            WorksOrderOperation.STATUS_STARTED,
            WorksOrderOperation.STATUS_WIP,
            WorksOrderOperation.STATUS_COMPLETED,
        }
        if open_statuses & production_statuses:
            return self.LINE_STATUS_WIP

        # Any op firmed by planner, or has planned dates → Firm Planned
        if any(
            op.status == WorksOrderOperation.STATUS_FIRMED or op.planned_date is not None
            for op in open_ops
        ):
            return self.LINE_STATUS_FIRM_PLANNED

        return self.LINE_STATUS_NEW

    @property
    def final_planned_date(self):
        """Latest planned_date across open operations — represents expected completion.
        Falls back to the line-level planned_date for lines with no operations."""
        dates = [
            op.planned_date for op in self.operations
            if op.planned_date and op.status != WorksOrderOperation.STATUS_CLOSED
        ]
        if dates:
            return max(dates)
        return self.planned_date

    def __repr__(self) -> str:
        return f"<SalesOrderLine {self.so_number}/{self.line_number}>"


# ---------------------------------------------------------------------------
# Works Order Operation
# ---------------------------------------------------------------------------

class WorksOrderOperation(db.Model):
    """
    Child record — one per Sales Order + line + work centre.

    These are the rows from OpenOrderBook_HIDE.csv.
    The ERP has already broken each order down by work centre — these ARE the
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
    STATUS_NEW_ORDER = "new_order"
    STATUS_FIRMED    = "firmed"
    STATUS_RELEASED  = "released"
    STATUS_WIP       = "wip"
    STATUS_COMPLETED = "completed"
    STATUS_CLOSED    = "closed"

    # Legacy aliases — keep so any stale DB rows still resolve correctly
    STATUS_NOT_STARTED = STATUS_NEW_ORDER
    STATUS_STARTED     = STATUS_RELEASED

    VALID_STATUSES = [
        STATUS_NEW_ORDER,
        STATUS_FIRMED,
        STATUS_RELEASED,
        STATUS_WIP,
        STATUS_COMPLETED,
        STATUS_CLOSED,
    ]

    # Status display labels and Bootstrap badge colours
    STATUS_META = {
        STATUS_NEW_ORDER: ("New Order", "light"),
        STATUS_FIRMED:    ("Firmed",    "secondary"),
        STATUS_RELEASED:  ("Released",  "primary"),
        STATUS_WIP:       ("WIP",       "warning"),
        STATUS_COMPLETED: ("Complete",  "success"),
        STATUS_CLOSED:    ("Closed",    "dark"),
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
# SMV Matrix
# ---------------------------------------------------------------------------

class SmvMatrix(db.Model):
    """
    Standard Minute Values — minutes per unit per department per product.

    Imported from SMVTable_HIDE.csv (wide format, unpivoted to long on import).
    UPSERT on import: preserves planner-set confidence level.

    Used for capacity load calculation:
        load_hours = SUM(operation.qty * smv_minutes / 60)
    """

    __tablename__ = "smv_matrix"
    __table_args__ = (
        db.UniqueConstraint("component_id", "department_id", name="uq_smv_comp_dept"),
    )

    CONFIDENCE_ESTIMATED = "estimated"
    CONFIDENCE_TIMED = "timed_study"
    CONFIDENCE_MOST = "most_study"

    id = db.Column(db.Integer, primary_key=True)
    component_id = db.Column(db.String(100), nullable=False, index=True)  # COMPONENT ID
    timing_code = db.Column(db.String(100), nullable=True)                # TIMING CODE (product category)
    description = db.Column(db.String(200), nullable=True)                # DESCRIPTION
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    smv_minutes = db.Column(db.Numeric(8, 3), nullable=True)              # SMV in minutes
    ops = db.Column(db.Integer, nullable=True)                            # OPS
    date_updated = db.Column(db.Date, nullable=True)                      # Date Updated
    confidence = db.Column(db.String(20), default=CONFIDENCE_ESTIMATED, nullable=False)

    last_modified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_modified_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    department = db.relationship("Department", back_populates="smv_entries")
    last_modified_by = db.relationship("User", foreign_keys=[last_modified_by_id])

    def __repr__(self) -> str:
        return f"<SmvMatrix {self.component_id} dept={self.department_id}>"


# ---------------------------------------------------------------------------
# Production Flow
# ---------------------------------------------------------------------------

class ProductionFlow(db.Model):
    """
    A production routing flow and its lead times per department stage.

    Imported from ProductionFlowLT_HIDE.csv (full replace).

    UNIQUE FLOW is a binary-encoded string identifying which departments a
    product passes through. dept_lead_times stores a JSON dict mapping
    department name to lead time in days.

    Used for backward scheduling:
        planned_date = due_date - sum(lead times for stages before this dept)
    """

    __tablename__ = "production_flows"

    id = db.Column(db.Integer, primary_key=True)
    unique_flow = db.Column(db.String(50), unique=True, nullable=False, index=True)  # UNIQUE FLOW
    flow_description = db.Column(db.String(500), nullable=True)                       # Production Flow
    ops = db.Column(db.Integer, nullable=True)                                        # Ops
    total_lead_time_days = db.Column(db.Integer, nullable=True)                       # Lead Time Q
    firmed = db.Column(db.Boolean, default=False, nullable=False)                     # Firmed? (Y/N)
    dept_lead_times = db.Column(db.JSON, nullable=True)                               # {dept_name: days}
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<ProductionFlow {self.unique_flow}>"


# ---------------------------------------------------------------------------
# Import Batch (audit log)
# ---------------------------------------------------------------------------

class ImportBatch(db.Model):
    """
    Audit record for every CSV file upload.

    Created at the start of each import, updated with results on completion.
    """

    __tablename__ = "import_batches"

    # Import type constants
    TYPE_OOB = "oob"
    TYPE_STOCK = "stock"
    TYPE_OPEN_PO = "open_po"
    TYPE_MAIN_MATERIAL = "main_material"
    TYPE_AS_MATERIAL = "as_material"
    TYPE_LABOUR_PLAN = "labour_plan"
    TYPE_SMV = "smv"
    TYPE_PRODUCTION_FLOW = "production_flow"

    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_PARTIAL = "partial"

    id = db.Column(db.Integer, primary_key=True)
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
