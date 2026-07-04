"""
Operations models.

WorksOrder       — live production jobs (bskyCOOISv3 BAQ).
ProductionOutput — daily labour/production records (PlanningOutPut BAQ).
"""

from app.extensions import db


class WorksOrder(db.Model):
    """Active production works order / assembly line. Full replace on every daily sync."""

    __tablename__ = "works_orders"

    id = db.Column(db.Integer, primary_key=True)
    job_num      = db.Column(db.String(20),  nullable=True, index=True)
    assembly_seq = db.Column(db.Integer,     nullable=True)
    job_released         = db.Column(db.Boolean, nullable=True)
    job_firm             = db.Column(db.Boolean, nullable=True)
    job_complete         = db.Column(db.Boolean, nullable=True, index=True)
    firm_order           = db.Column(db.Boolean, nullable=True)
    firm_line            = db.Column(db.Boolean, nullable=True)
    order_held           = db.Column(db.Boolean, nullable=True)
    so_credit_hold       = db.Column(db.Boolean, nullable=True)
    customer_credit_hold = db.Column(db.Boolean, nullable=True)
    ship_order_complete  = db.Column(db.Boolean, nullable=True)
    guaranteed_christmas = db.Column(db.Boolean, nullable=True)
    display_order        = db.Column(db.Boolean, nullable=True)
    req_due_date                = db.Column(db.Date, nullable=True, index=True)
    start_date                  = db.Column(db.Date, nullable=True)
    load_date                   = db.Column(db.String(50), nullable=True)
    req_date                    = db.Column(db.Date, nullable=True)
    original_ship_by            = db.Column(db.Date, nullable=True)
    original_need_by            = db.Column(db.Date, nullable=True)
    customer_delivery_requested = db.Column(db.Date, nullable=True)
    order_received_date         = db.Column(db.Date, nullable=True)
    last_xmas_order_date        = db.Column(db.Date, nullable=True)
    last_xmas_delivery          = db.Column(db.Date, nullable=True)
    prod_plnwk   = db.Column(db.String(20),  nullable=True, index=True)
    order_sort   = db.Column(db.Integer,     nullable=True)
    customer_id      = db.Column(db.String(20),  nullable=True, index=True)
    customer_name    = db.Column(db.String(150), nullable=True)
    so_type          = db.Column(db.String(20),  nullable=True)
    so_type_desc     = db.Column(db.String(100), nullable=True)
    order_num        = db.Column(db.Integer,     nullable=True, index=True)
    order_line       = db.Column(db.Integer,     nullable=True)
    order_rel_num    = db.Column(db.Integer,     nullable=True)
    ship_to_name     = db.Column(db.String(150), nullable=True)
    ship_to_zip      = db.Column(db.String(20),  nullable=True)
    order_book_comments = db.Column(db.Text,     nullable=True)
    grn              = db.Column(db.String(50),  nullable=True)
    net_unit_price     = db.Column(db.Numeric(14, 4), nullable=True)
    net_unit_price_gbp = db.Column(db.Numeric(14, 4), nullable=True)
    part_num      = db.Column(db.String(50),  nullable=True, index=True)
    description   = db.Column(db.String(200), nullable=True)
    class_id      = db.Column(db.String(50),  nullable=True, index=True)
    comment_text  = db.Column(db.Text,        nullable=True)
    model         = db.Column(db.String(100), nullable=True)
    size          = db.Column(db.String(50),  nullable=True)
    size_desc     = db.Column(db.String(100), nullable=True)
    prod_size     = db.Column(db.String(100), nullable=True)
    cover         = db.Column(db.String(100), nullable=True)
    cover_type    = db.Column(db.String(100), nullable=True)
    leg           = db.Column(db.String(50),  nullable=True)
    leg_mtl       = db.Column(db.String(50),  nullable=True)
    castor_mtl    = db.Column(db.String(50),  nullable=True)
    castor_desc   = db.Column(db.String(100), nullable=True)
    stud1_mtl     = db.Column(db.String(50),  nullable=True)
    stud2_mtl     = db.Column(db.String(50),  nullable=True)
    seat_interior_mtl = db.Column(db.String(50), nullable=True)
    back_interior_mtl = db.Column(db.String(50), nullable=True)
    scat_interior_mtl = db.Column(db.String(50), nullable=True)
    material_1 = db.Column(db.String(50), nullable=True)
    material_1_desc = db.Column(db.String(100), nullable=True)
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
    required_qty  = db.Column(db.Numeric(12, 3), nullable=True)
    qty_completed = db.Column(db.Numeric(12, 3), nullable=True)
    selling_qty   = db.Column(db.Numeric(12, 3), nullable=True)
    shipped_qty   = db.Column(db.Numeric(12, 3), nullable=True)
    next_op       = db.Column(db.String(20),  nullable=True)
    wip_warehouse = db.Column(db.String(20),  nullable=True)
    wip_bin       = db.Column(db.String(20),  nullable=True)
    waiting_temp  = db.Column(db.Boolean,     nullable=True)
    mtl_shortage  = db.Column(db.Boolean,     nullable=True)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<WorksOrder {self.job_num}/{self.assembly_seq}>"


class ProductionOutput(db.Model):
    """
    Daily production output record from the PlanningOutPut BAQ.

    Insert-only: RowIdent (UUID per labour diary entry) is used as the
    natural key. Re-syncing the same date range skips already-present rows.
    """

    __tablename__ = "production_output"

    id = db.Column(db.Integer, primary_key=True)
    row_ident    = db.Column(db.String(36),  nullable=True, index=True)  # RowIdent (not unique - Epicor uses fake sequential GUIDs)
    job_num      = db.Column(db.String(20),  nullable=True, index=True)
    assembly_seq = db.Column(db.Integer,     nullable=True)
    opr_seq      = db.Column(db.Integer,     nullable=True)
    op_desc      = db.Column(db.String(50),  nullable=True, index=True)
    employee_num = db.Column(db.String(20),  nullable=True, index=True)
    labor_entry_method = db.Column(db.String(20), nullable=True)
    clock_in_date = db.Column(db.Date, nullable=True, index=True)
    labor_qty     = db.Column(db.Numeric(12, 3), nullable=True)
    prod_plnwk    = db.Column(db.String(20),  nullable=True, index=True)
    model         = db.Column(db.String(100), nullable=True)
    mod_size      = db.Column(db.String(100), nullable=True)
    line_desc     = db.Column(db.String(255), nullable=True)
    assembly_desc = db.Column(db.String(200), nullable=True)
    currency_code     = db.Column(db.String(10),    nullable=True)
    exchange_rate     = db.Column(db.Numeric(12,6), nullable=True)
    release_price     = db.Column(db.Numeric(14,4), nullable=True)
    release_discount  = db.Column(db.Numeric(14,4), nullable=True)
    misc_charges      = db.Column(db.Numeric(14,4), nullable=True)
    release_total     = db.Column(db.Numeric(14,4), nullable=True)
    release_total_gbp = db.Column(db.Numeric(14,4), nullable=True)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<ProductionOutput {self.job_num} {self.op_desc} {self.clock_in_date}>"
