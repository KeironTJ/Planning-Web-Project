"""
Operations works order models.

WorksOrder — one row per job + assembly line, sourced from the bskyCOOISv3 BAQ.
Full replace on every Epicor API sync.
"""

from datetime import datetime, timezone

from app.extensions import db


class WorksOrder(db.Model):
    """
    Active production works order / assembly line from the COOIS BAQ.

    Granularity: one row per JobHead + AssemblySeq.
    Field names map directly to BAQ field names for clarity.
    Full replace on every daily sync.
    """

    __tablename__ = "works_orders"

    id = db.Column(db.Integer, primary_key=True)

    # --- Job identity ---
    job_num      = db.Column(db.String(20),  nullable=True, index=True)  # JobHead_JobNum
    assembly_seq = db.Column(db.Integer,     nullable=True)              # JobAsmbl_AssemblySeq

    # --- Status flags ---
    job_released  = db.Column(db.Boolean, nullable=True)                 # JobHead_JobReleased
    job_firm      = db.Column(db.Boolean, nullable=True)                 # JobHead_JobFirm
    job_complete  = db.Column(db.Boolean, nullable=True, index=True)     # JobHead_JobComplete
    firm_order    = db.Column(db.Boolean, nullable=True)                 # OrderHed_FirmOrder_c
    firm_line     = db.Column(db.Boolean, nullable=True)                 # OrderDtl_FirmLine_c
    order_held    = db.Column(db.Boolean, nullable=True)                 # OrderHed_OrderHeld
    so_credit_hold= db.Column(db.Boolean, nullable=True)                 # Calculated_SOCreditHold
    customer_credit_hold = db.Column(db.Boolean, nullable=True)          # Customer_CreditHold
    ship_order_complete  = db.Column(db.Boolean, nullable=True)          # OrderHed_ShipOrderComplete
    guaranteed_christmas = db.Column(db.Boolean, nullable=True)          # OrderHed_GuaranteedChristmasDelivery_c
    display_order = db.Column(db.Boolean, nullable=True)                 # OrderHed_DisplayOrder_c

    # --- Dates ---
    req_due_date  = db.Column(db.Date, nullable=True, index=True)        # JobHead_ReqDueDate
    start_date    = db.Column(db.Date, nullable=True)                    # JobHead_StartDate
    load_date     = db.Column(db.String(50), nullable=True)              # JobHead_LoadDate_c
    req_date      = db.Column(db.Date, nullable=True)                    # OrderRel_ReqDate
    original_ship_by     = db.Column(db.Date, nullable=True)             # OrderRel_OriginalShipBy_c
    original_need_by     = db.Column(db.Date, nullable=True)             # OrderRel_OriginalNeedBy_c
    customer_delivery_requested = db.Column(db.Date, nullable=True)      # OrderHed_CustomerDeliveryDateRequested_c
    order_received_date  = db.Column(db.Date, nullable=True)             # OrderHed_OrderReceivedDate_c
    last_xmas_order_date = db.Column(db.Date, nullable=True)             # Customer_LastOrderReceivedDateGuaranteedChristmas_c
    last_xmas_delivery   = db.Column(db.Date, nullable=True)             # Customer_LastChristmasDeliveryDate_c

    # --- Production planning ---
    prod_plnwk   = db.Column(db.String(20), nullable=True, index=True)   # JobHead_ProdPlnWk_c
    order_sort   = db.Column(db.Integer,    nullable=True)               # Calculated_OrderSort

    # --- Customer / order ---
    customer_id  = db.Column(db.String(20),  nullable=True, index=True)  # Customer_CustID
    customer_name= db.Column(db.String(150), nullable=True)              # Customer_Name
    so_type      = db.Column(db.String(20),  nullable=True)              # OrderHed_SOType_c
    so_type_desc = db.Column(db.String(100), nullable=True)              # UDCodes_CodeDesc
    order_num    = db.Column(db.Integer,     nullable=True, index=True)  # JobProd_OrderNum
    order_line   = db.Column(db.Integer,     nullable=True)              # JobProd_OrderLine
    order_rel_num= db.Column(db.Integer,     nullable=True)              # JobProd_OrderRelNum
    ship_to_name = db.Column(db.String(150), nullable=True)              # ShipTo_Name
    ship_to_zip  = db.Column(db.String(20),  nullable=True)              # ShipTo_ZIP
    order_book_comments = db.Column(db.Text, nullable=True)              # OrderHed_OrderBookComments_c
    grn          = db.Column(db.String(50),  nullable=True)              # Calculated_GRN
    net_unit_price     = db.Column(db.Numeric(14, 4), nullable=True)     # Calculated_NetUnitPrice01
    net_unit_price_gbp = db.Column(db.Numeric(14, 4), nullable=True)     # Calculated_NetUnitPriceGBP

    # --- Part / product identity ---
    part_num      = db.Column(db.String(50),  nullable=True, index=True) # JobAsmbl_PartNum
    description   = db.Column(db.String(255), nullable=True)             # JobAsmbl_Description
    class_id      = db.Column(db.String(50),  nullable=True, index=True) # Part_ClassID
    comment_text  = db.Column(db.Text,        nullable=True)             # JobAsmbl_CommentText

    # --- Product configuration ---
    model         = db.Column(db.String(100), nullable=True)             # Calculated_Model
    size          = db.Column(db.String(50),  nullable=True)             # Calculated_Size
    size_desc     = db.Column(db.String(100), nullable=True)             # Calculated_SizeDesc
    prod_size     = db.Column(db.String(100), nullable=True)             # Calculated_ProdSize
    cover         = db.Column(db.String(100), nullable=True)             # Calculated_Cover
    cover_type    = db.Column(db.String(100), nullable=True)             # Calculated_CoverType
    leg           = db.Column(db.String(50),  nullable=True)             # Calculated_Leg
    leg_mtl       = db.Column(db.String(50),  nullable=True)             # Calculated_LegMtl
    castor_mtl    = db.Column(db.String(50),  nullable=True)             # Calculated_CastorMtl
    castor_desc   = db.Column(db.String(100), nullable=True)             # Calculated_CastorDesc
    stud1_mtl     = db.Column(db.String(50),  nullable=True)             # Calculated_Stud1Mtl
    stud2_mtl     = db.Column(db.String(50),  nullable=True)             # Calculated_Stud2Mtl
    seat_interior_mtl = db.Column(db.String(50), nullable=True)          # Calculated_SeatInteriorMtl
    back_interior_mtl = db.Column(db.String(50), nullable=True)          # Calculated_BackInteriorMtl
    scat_interior_mtl = db.Column(db.String(50), nullable=True)          # Calculated_ScatInteriorMtl

    # --- Materials (up to 8 codes + descriptions) ---
    material_1 = db.Column(db.String(50), nullable=True)                 # Calculated_Material1
    material_1_desc = db.Column(db.String(100), nullable=True)           # Calculated_Material1Desc
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
    required_qty  = db.Column(db.Numeric(12, 3), nullable=True)          # JobAsmbl_RequiredQty
    qty_completed = db.Column(db.Numeric(12, 3), nullable=True)          # JobHead_QtyCompleted
    selling_qty   = db.Column(db.Numeric(12, 3), nullable=True)          # Calculated_SellingQty
    shipped_qty   = db.Column(db.Numeric(12, 3), nullable=True)          # Calculated_ShippedQty01

    # --- Shop floor status ---
    next_op       = db.Column(db.String(20),  nullable=True)             # Calculated_NextOp01
    wip_warehouse = db.Column(db.String(20),  nullable=True)             # PartWip_WareHouseCode
    wip_bin       = db.Column(db.String(20),  nullable=True)             # PartWip_BinNum
    waiting_temp  = db.Column(db.Boolean,     nullable=True)             # JobHead_WaitingTemp_c
    mtl_shortage  = db.Column(db.Boolean,     nullable=True)             # JobHead_MtlShortage_c

    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<WorksOrder {self.job_num}/{self.assembly_seq}>"


    id = db.Column(db.Integer, primary_key=True)

    # --- Job identity ---
    job_num      = db.Column(db.String(20),  nullable=True, index=True)  # JobHead_JobNum
    assembly_seq = db.Column(db.Integer,     nullable=True)              # JobAsmbl_AssemblySeq

    # --- Status flags ---
    job_released = db.Column(db.Boolean, nullable=True)                  # JobHead_JobReleased
    job_firm     = db.Column(db.Boolean, nullable=True)                  # JobHead_JobFirm
    job_complete = db.Column(db.Boolean, nullable=True, index=True)      # JobHead_JobComplete

    # --- Dates ---
    req_due_date = db.Column(db.Date, nullable=True, index=True)         # JobHead_ReqDueDate
    start_date   = db.Column(db.Date, nullable=True)                     # JobHead_StartDate
    load_date    = db.Column(db.String(50), nullable=True)               # JobHead_LoadDate_c
    req_date     = db.Column(db.Date, nullable=True)                     # OrderRel_ReqDate

    # --- Production planning ---
    prod_plnwk   = db.Column(db.String(20), nullable=True, index=True)   # JobHead_ProdPlnWk_c

    # --- Customer / order ---
    customer_id  = db.Column(db.String(20),  nullable=True, index=True)  # Customer_CustID
    customer_name= db.Column(db.String(150), nullable=True)              # Customer_Name
    so_type      = db.Column(db.String(20),  nullable=True)              # OrderHed_SOType_c
    order_num    = db.Column(db.Integer,     nullable=True, index=True)  # JobProd_OrderNum
    order_line   = db.Column(db.Integer,     nullable=True)              # JobProd_OrderLine

    # --- Part / product ---
    part_num      = db.Column(db.String(50),  nullable=True, index=True) # JobAsmbl_PartNum
    description   = db.Column(db.String(200), nullable=True)             # JobAsmbl_Description
    class_id      = db.Column(db.String(50),  nullable=True, index=True) # Part_ClassID
    model         = db.Column(db.String(100), nullable=True)             # Calculated_Model
    size          = db.Column(db.String(50),  nullable=True)             # Calculated_Size
    cover         = db.Column(db.String(100), nullable=True)             # Calculated_Cover
    cover_type    = db.Column(db.String(100), nullable=True)             # Calculated_CoverType

    # --- Materials (up to 8 fabric/material codes) ---
    material_1 = db.Column(db.String(50), nullable=True)                 # Calculated_Material1
    material_2 = db.Column(db.String(50), nullable=True)
    material_3 = db.Column(db.String(50), nullable=True)
    material_4 = db.Column(db.String(50), nullable=True)
    material_5 = db.Column(db.String(50), nullable=True)
    material_6 = db.Column(db.String(50), nullable=True)
    material_7 = db.Column(db.String(50), nullable=True)
    material_8 = db.Column(db.String(50), nullable=True)

    # --- Quantities ---
    required_qty  = db.Column(db.Numeric(12, 3), nullable=True)          # JobAsmbl_RequiredQty
    qty_completed = db.Column(db.Numeric(12, 3), nullable=True)          # JobHead_QtyCompleted
    selling_qty   = db.Column(db.Numeric(12, 3), nullable=True)          # Calculated_SellingQty
    shipped_qty   = db.Column(db.Numeric(12, 3), nullable=True)          # Calculated_ShippedQty01

    # --- Shop floor status ---
    next_op       = db.Column(db.String(20),  nullable=True)             # Calculated_NextOp01
    wip_warehouse = db.Column(db.String(20),  nullable=True)             # PartWip_WareHouseCode
    wip_bin       = db.Column(db.String(20),  nullable=True)             # PartWip_BinNum
    waiting_temp  = db.Column(db.Boolean,     nullable=True)             # JobHead_WaitingTemp_c
    mtl_shortage  = db.Column(db.Boolean,     nullable=True)             # JobHead_MtlShortage_c

    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<WorksOrder {self.job_num}/{self.assembly_seq}>"
