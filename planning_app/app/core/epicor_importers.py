"""
Epicor BAQ importers — one class per BAQ, plus a registry for batch running.

Workflow for each new importer once added to the registry:
  1. Discover fields:    flask epicor inspect <key>
  2. Create the model:   app/<module>/models.py
  3. Implement _sync_records() below — BAQ field names drive the schema
  4. Run it:             flask epicor sync <key>

Running importers
-----------------
All at once:           flask epicor sync
Specific importers:    flask epicor sync stock purchase_orders
Single importer:       flask epicor sync works_orders
List all available:    flask epicor list
"""

from __future__ import annotations

from datetime import date, datetime

from app.core.epicor_sync import EpicorBaqImporter
from app.sales.orders.models import ImportBatch


# ---------------------------------------------------------------------------
# Stock on Hand   →   BAQ: PlanningStockReport
# ---------------------------------------------------------------------------

class StockImporter(EpicorBaqImporter):
    """
    Syncs current stock on hand.

    Full replace on every run (truncate + reload).
    """

    BAQ_NAME = "PlanningStockReport"
    IMPORT_TYPE = "epicor_stock"
    BAQ_PARAMS = {"JobReqByDateSTKPLAN": ""}   # required param; empty string = no date filter

    def _target_table(self) -> str:
        return "stock"

    def _sync_records(
        self, records: list[dict], batch: ImportBatch, now: datetime
    ) -> None:
        from decimal import Decimal, InvalidOperation

        from app.extensions import db
        from app.purchasing.materials.models import Stock

        def _dec(val):
            """Coerce a BAQ value to Decimal, returning None on blank/null."""
            if val is None or val == "":
                return None
            try:
                return Decimal(str(val))
            except InvalidOperation:
                return None

        def _bool(val):
            """Coerce a BAQ value to bool (handles Python bool, int, or string)."""
            if isinstance(val, bool):
                return val
            if isinstance(val, int):
                return bool(val)
            if isinstance(val, str):
                return val.strip().lower() in ("true", "1", "yes")
            return None

        Stock.query.delete()
        db.session.flush()

        for r in records:
            db.session.add(Stock(
                part_num                  = r.get("Part_PartNum") or "",
                part_description          = r.get("Part_PartDescription") or None,
                class_id                  = r.get("Part_ClassID") or None,
                unit_of_measure           = r.get("Part_IUM") or None,
                plant                     = r.get("PartPlant_Plant") or None,
                qty_on_hand               = _dec(r.get("Calculated_TotalOnHand2")),
                qty_on_hand_stores        = _dec(r.get("Calculated_TotalOnHandSTORES")),
                qty_on_hand_prod_uk       = _dec(r.get("Calculated_TotalOnHandPRODUK")),
                qty_on_hand_romania       = _dec(r.get("Calculated_TotalOnHandROMANIA")),
                qty_on_hand_others        = _dec(r.get("Calculated_TotalOnHandOTHERS")),
                qty_required              = _dec(r.get("Calculated_TOTALRequiredQty2")),
                qty_required_unreleased   = _dec(r.get("Calculated_TOTALUnRelRequiredQty2")),
                qty_open_po               = _dec(r.get("Calculated_OpenPOQty")),
                qty_inspection            = _dec(r.get("Calculated_InspQty")),
                surplus_deficit           = _dec(r.get("Calculated_SurplusDeficitStock")),
                surplus_deficit_unreleased= _dec(r.get("Calculated_SurplusDeficitStockUR")),
                insufficient_stock        = _bool(r.get("Calculated_InsufficientStock")),
                imported_at               = now,
            ))

        batch.rows_inserted = len(records)


# ---------------------------------------------------------------------------
# Purchase Orders   →   BAQ: OSPurchaseOrders
# ---------------------------------------------------------------------------

class PurchaseOrderImporter(EpicorBaqImporter):
    """
    Syncs open inbound purchase orders.

    Full replace on every run (truncate + reload).
    """

    BAQ_NAME = "OSPurchaseOrders"
    IMPORT_TYPE = "epicor_purchase_orders"
    # No static params needed — BAQ returns all open POs without filtering.
    # Pass params at runtime if you need to filter: importer.run(params={"PartNum": "ABC"})
    BAQ_PARAMS = {}

    def _target_table(self) -> str:
        return "purchase_orders"


    def _sync_records(
        self, records: list[dict], batch: ImportBatch, now: datetime
    ) -> None:
        from datetime import datetime as _dt
        from decimal import Decimal, InvalidOperation

        from app.extensions import db
        from app.purchasing.materials.models import PurchaseOrder

        def _dec(val):
            if val is None or val == "":
                return None
            try:
                return Decimal(str(val))
            except InvalidOperation:
                return None

        def _date(val):
            """Parse Epicor ISO datetime string to a date object."""
            if not val:
                return None
            try:
                return _dt.fromisoformat(val.replace("Z", "+00:00")).date()
            except (ValueError, AttributeError):
                return None

        PurchaseOrder.query.delete()
        db.session.flush()

        for r in records:
            db.session.add(PurchaseOrder(
                po_num          = r.get("PORel_PONum"),
                po_line         = r.get("PORel_POLine"),
                po_release      = r.get("PORel_PORelNum"),
                open_order      = r.get("POHeader_OpenOrder"),
                open_line       = r.get("PODetail_OpenLine"),
                open_release    = r.get("PORel_OpenRelease"),
                order_date      = _date(r.get("POHeader_OrderDate")),
                due_date        = _date(r.get("PORel_DueDate")),
                promise_date    = _date(r.get("PORel_PromiseDt")),
                part_num        = r.get("PODetail_PartNum") or None,
                line_desc       = r.get("PODetail_LineDesc") or None,
                unit_of_measure = r.get("PODetail_PUM") or None,
                rel_qty         = _dec(r.get("PORel_RelQty")),
                arrived_qty     = _dec(r.get("PORel_ArrivedQty")),
                received_qty    = _dec(r.get("PORel_ReceivedQty")),
                outstanding_qty = _dec(r.get("Calculated_OutstandingQty")),
                invoiced_qty    = _dec(r.get("PORel_InvoicedQty")),
                unit_cost       = _dec(r.get("PODetail_UnitCost")),
                doc_unit_cost   = _dec(r.get("PODetail_DocUnitCost")),
                cost_per_code   = r.get("PODetail_CostPerCode") or None,
                currency_code   = r.get("POHeader_CurrencyCode") or None,
                exchange_rate   = _dec(r.get("POHeader_ExchangeRate")),
                supplier_id     = r.get("Vendor_VendorID") or None,
                supplier_name   = r.get("Vendor_Name") or None,
                imported_at     = now,
            ))

        batch.rows_inserted = len(records)


# ---------------------------------------------------------------------------
# Material Requirements   →   BAQ: PlanningMatReq
# ---------------------------------------------------------------------------

class MaterialRequirementsImporter(EpicorBaqImporter):
    """
    Syncs MRP material requirements.

    Full replace on every run (truncate + reload).
    If the BAQ needs a date range, add params when calling .run():
        MaterialRequirementsImporter(client).run(params={"DateFrom": "2026-01-01"})
    Or add static defaults to BAQ_PARAMS below.
    """

    BAQ_NAME = "PlanningMatReq"
    IMPORT_TYPE = "epicor_material_requirements"
    # JobReqByDateMat cannot be blank via REST — use today via get_dynamic_params().
    # Override at call time to change the horizon: importer.run(params={"JobReqByDateMat": "2026-01-01"})
    BAQ_PARAMS = {}

    def _target_table(self) -> str:
        return "material_requirements"

    def get_dynamic_params(self) -> dict:
        """
        No params needed — BAQ returns all open requirements when called without
        a date filter (now that the $top pagination fix is in place).

        NOTE: Passing a far-future date causes Epicor server-side timeout on
        large result sets. No-param is the correct approach for this BAQ.
        If the BAQ is later modified to accept blank as 'no filter', add:
            BAQ_PARAMS = {"JobReqByDateMat": ""}
        and remove this override.
        """
        return {}

    def _sync_records(
        self, records: list[dict], batch: ImportBatch, now: datetime
    ) -> None:
        from datetime import datetime as _dt
        from decimal import Decimal, InvalidOperation

        from app.extensions import db
        from app.purchasing.materials.models import MaterialRequirementMain

        def _dec(val):
            if val is None or val == "":
                return None
            try:
                return Decimal(str(val))
            except InvalidOperation:
                return None

        def _date(val):
            if not val:
                return None
            try:
                return _dt.fromisoformat(val.replace("Z", "+00:00")).date()
            except (ValueError, AttributeError):
                return None

        MaterialRequirementMain.query.delete()
        db.session.flush()

        for r in records:
            db.session.add(MaterialRequirementMain(
                works_order        = r.get("JobHead_JobNum") or None,
                job_released       = r.get("JobHead_JobReleased"),
                job_firm           = r.get("JobHead_JobFirm"),
                job_complete       = r.get("JobHead_JobComplete"),
                job_closed         = r.get("JobHead_JobClosed"),
                due_date           = _date(r.get("JobHead_ReqDueDate")),
                finished_part_num  = r.get("JobHead_PartNum") or None,
                finished_part_desc = r.get("JobHead_PartDescription") or None,
                prod_qty           = _dec(r.get("JobHead_ProdQty")),
                plant              = r.get("JobHead_Plant") or None,
                prod_plnwk         = r.get("JobHead_ProdPlnWk_c") or None,
                model              = r.get("JobHead_Cnfg_Model_c") or None,
                size               = r.get("Calculated_Size") or None,
                so_type            = r.get("OrderHed_SOType_c") or None,
                so_number          = str(r["OrderHed_OrderNum"]) if r.get("OrderHed_OrderNum") else None,
                assembly_seq       = r.get("JobAsmbl_AssemblySeq"),
                assembly_desc      = r.get("JobAsmbl_Description") or None,
                mtl_seq            = r.get("JobMtl_MtlSeq"),
                material_code      = r.get("JobMtl_PartNum") or None,
                material_description = r.get("JobMtl_Description") or None,
                backflush          = r.get("JobMtl_BackFlush"),
                qty_per            = _dec(r.get("JobMtl_QtyPer")),
                qty_for_order      = _dec(r.get("JobMtl_RequiredQty")),
                qty_issued         = _dec(r.get("JobMtl_IssuedQty")),
                issued_complete    = r.get("JobMtl_IssuedComplete"),
                related_operation  = r.get("JobMtl_RelatedOperation"),
                warehouse_code     = r.get("JobMtl_WarehouseCode") or None,
                class_id           = r.get("Part_ClassID") or None,
                imported_at        = now,
            ))

        batch.rows_inserted = len(records)


# ---------------------------------------------------------------------------
# Works Orders / Operations   →   BAQ: bskyCOOISv3
# ---------------------------------------------------------------------------

class WorksOrderImporter(EpicorBaqImporter):
    """
    Syncs works order operations (COOIS equivalent).

    Full replace on every run (truncate + reload).
    If the BAQ needs a date range, add params when calling .run():
        WorksOrderImporter(client).run(params={"DateFrom": "2026-01-01"})
    """

    BAQ_NAME = "bskyCOOISv3"
    IMPORT_TYPE = "epicor_works_orders"
    # Only the production plan range needs to be set — all other params (JobFirm,
    # JobComplete, JobReleased, OrderNum, etc.) default to NULL when omitted,
    # which the BAQ treats as "no filter". ProdPlanFrom/To must be provided as
    # strings to avoid NULL breaking the >= / <= comparisons.
    # Only the production plan range needs to be provided — all other params
    # (JobFirm, JobComplete, JobReleased, etc.) default to NULL when omitted,
    # which the BAQ treats as "no filter".
    BAQ_PARAMS = {
        "ProdPlanFrom": "0",        # less than any real week value → no lower bound
        "ProdPlanTo":   "9999999",  # greater than any real week value → no upper bound
    }
    # bskyCOOISv3 ignores $top/$skip and always returns the full result set in one go.
    # PAGE_SIZE must exceed the total row count so the pagination loop exits after the
    # first (and only) call, rather than looping forever.
    PAGE_SIZE = 10000

    def _target_table(self) -> str:
        return "works_orders"

    def _sync_records(
        self, records: list[dict], batch: ImportBatch, now: datetime
    ) -> None:
        from datetime import datetime as _dt
        from decimal import Decimal, InvalidOperation

        from app.extensions import db
        from app.operations.models import WorksOrder

        def _dec(val):
            if val is None or val == "":
                return None
            try:
                return Decimal(str(val))
            except InvalidOperation:
                return None

        def _date(val):
            if not val:
                return None
            try:
                return _dt.fromisoformat(val.replace("Z", "+00:00")).date()
            except (ValueError, AttributeError):
                return None

        def _bool(val):
            if isinstance(val, bool):
                return val
            if isinstance(val, (int, float)):
                return bool(val)
            if isinstance(val, str):
                return val.strip().lower() in ("true", "1", "yes")
            return None

        WorksOrder.query.delete()
        db.session.flush()

        seen: set = set()
        skipped = 0
        for r in records:
            key = (r.get("JobHead_JobNum"), r.get("JobAsmbl_AssemblySeq"))
            if key in seen:
                skipped += 1
                continue
            seen.add(key)

            db.session.add(WorksOrder(
                job_num       = r.get("JobHead_JobNum") or None,
                assembly_seq  = r.get("JobAsmbl_AssemblySeq"),
                job_released  = _bool(r.get("JobHead_JobReleased")),
                job_firm      = _bool(r.get("JobHead_JobFirm")),
                job_complete  = _bool(r.get("JobHead_JobComplete")),
                firm_order    = _bool(r.get("OrderHed_FirmOrder_c")),
                firm_line     = _bool(r.get("OrderDtl_FirmLine_c")),
                order_held    = _bool(r.get("OrderHed_OrderHeld")),
                so_credit_hold= _bool(r.get("Calculated_SOCreditHold")),
                customer_credit_hold = _bool(r.get("Customer_CreditHold")),
                ship_order_complete  = _bool(r.get("OrderHed_ShipOrderComplete")),
                guaranteed_christmas = _bool(r.get("OrderHed_GuaranteedChristmasDelivery_c")),
                display_order = _bool(r.get("OrderHed_DisplayOrder_c")),
                req_due_date  = _date(r.get("JobHead_ReqDueDate")),
                start_date    = _date(r.get("JobHead_StartDate")),
                load_date     = r.get("JobHead_LoadDate_c") or None,
                req_date      = _date(r.get("OrderRel_ReqDate")),
                original_ship_by     = _date(r.get("OrderRel_OriginalShipBy_c")),
                original_need_by     = _date(r.get("OrderRel_OriginalNeedBy_c")),
                customer_delivery_requested = _date(r.get("OrderHed_CustomerDeliveryDateRequested_c")),
                order_received_date  = _date(r.get("OrderHed_OrderReceivedDate_c")),
                last_xmas_order_date = _date(r.get("Customer_LastOrderReceivedDateGuaranteedChristmas_c")),
                last_xmas_delivery   = _date(r.get("Customer_LastChristmasDeliveryDate_c")),
                prod_plnwk    = r.get("JobHead_ProdPlnWk_c") or None,
                order_sort    = r.get("Calculated_OrderSort"),
                customer_id   = r.get("Customer_CustID") or None,
                customer_name = r.get("Customer_Name") or None,
                so_type       = r.get("OrderHed_SOType_c") or None,
                so_type_desc  = r.get("UDCodes_CodeDesc") or None,
                order_num     = r.get("JobProd_OrderNum"),
                order_line    = r.get("JobProd_OrderLine"),
                order_rel_num = r.get("JobProd_OrderRelNum"),
                ship_to_name  = r.get("ShipTo_Name") or None,
                ship_to_zip   = r.get("ShipTo_ZIP") or None,
                order_book_comments = r.get("OrderHed_OrderBookComments_c") or None,
                grn           = r.get("Calculated_GRN") or None,
                net_unit_price     = _dec(r.get("Calculated_NetUnitPrice01")),
                net_unit_price_gbp = _dec(r.get("Calculated_NetUnitPriceGBP")),
                part_num      = r.get("JobAsmbl_PartNum") or None,
                description   = r.get("JobAsmbl_Description") or None,
                class_id      = r.get("Part_ClassID") or None,
                comment_text  = r.get("JobAsmbl_CommentText") or None,
                model         = r.get("Calculated_Model") or None,
                size          = r.get("Calculated_Size") or None,
                size_desc     = r.get("Calculated_SizeDesc") or None,
                prod_size     = r.get("Calculated_ProdSize") or None,
                cover         = r.get("Calculated_Cover") or None,
                cover_type    = r.get("Calculated_CoverType") or None,
                leg           = r.get("Calculated_Leg") or None,
                leg_mtl       = r.get("Calculated_LegMtl") or None,
                castor_mtl    = r.get("Calculated_CastorMtl") or None,
                castor_desc   = r.get("Calculated_CastorDesc") or None,
                stud1_mtl     = r.get("Calculated_Stud1Mtl") or None,
                stud2_mtl     = r.get("Calculated_Stud2Mtl") or None,
                seat_interior_mtl = r.get("Calculated_SeatInteriorMtl") or None,
                back_interior_mtl = r.get("Calculated_BackInteriorMtl") or None,
                scat_interior_mtl = r.get("Calculated_ScatInteriorMtl") or None,
                material_1    = r.get("Calculated_Material1") or None,
                material_1_desc = r.get("Calculated_Material1Desc") or None,
                material_2    = r.get("Calculated_Material2") or None,
                material_2_desc = r.get("Calculated_Material2Desc") or None,
                material_3    = r.get("Calculated_Material3") or None,
                material_3_desc = r.get("Calculated_Material3Desc") or None,
                material_4    = r.get("Calculated_Material4") or None,
                material_4_desc = r.get("Calculated_Material4Desc") or None,
                material_5    = r.get("Calculated_Material5") or None,
                material_5_desc = r.get("Calculated_Material5Desc") or None,
                material_6    = r.get("Calculated_Material6") or None,
                material_6_desc = r.get("Calculated_Material6Desc") or None,
                material_7    = r.get("Calculated_Material7") or None,
                material_7_desc = r.get("Calculated_Material7Desc") or None,
                material_8    = r.get("Calculated_Material8") or None,
                material_8_desc = r.get("Calculated_Material8Desc") or None,
                required_qty  = _dec(r.get("JobAsmbl_RequiredQty")),
                qty_completed = _dec(r.get("JobHead_QtyCompleted")),
                selling_qty   = _dec(r.get("Calculated_SellingQty")),
                shipped_qty   = _dec(r.get("Calculated_ShippedQty01")),
                next_op       = r.get("Calculated_NextOp01") or None,
                wip_warehouse = r.get("PartWip_WareHouseCode") or None,
                wip_bin       = r.get("PartWip_BinNum") or None,
                waiting_temp  = _bool(r.get("JobHead_WaitingTemp_c")),
                mtl_shortage  = _bool(r.get("JobHead_MtlShortage_c")),
                imported_at   = now,
            ))

        batch.rows_inserted = len(records) - skipped
        if skipped:
            batch.notes = f"{skipped} duplicate rows skipped"


# ---------------------------------------------------------------------------
# Sales Orders   →   BAQ: bskyVA05v1  (open + closed, same table)
# ---------------------------------------------------------------------------

def _build_sales_order(r: dict, _date, _dec, _bool, now):
    """Map one BAQ record to a SalesOrder keyword-arg dict."""
    from app.sales.orders.models import SalesOrder
    return SalesOrder(
        order_num    = r.get("OrderHed_OrderNum"),
        order_line   = r.get("OrderDtl_OrderLine"),
        rel_num      = r.get("OrderRel_OrderRelNum"),
        po_num       = r.get("OrderHed_PONum") or None,
        open_order   = _bool(r.get("OrderHed_OpenOrder")),
        open_line    = _bool(r.get("OrderDtl_OpenLine")),
        open_release = _bool(r.get("OrderRel_OpenRelease")),
        firm_order   = _bool(r.get("OrderHed_FirmOrder_c")),
        firm_line    = _bool(r.get("OrderDtl_FirmLine_c")),
        firm_release = _bool(r.get("OrderRel_FirmRelease")),
        void_line    = _bool(r.get("OrderDtl_VoidLine")),
        order_held   = _bool(r.get("OrderHed_OrderHeld")),
        so_credit_hold       = _bool(r.get("Calculated_SOCreditHold")),
        customer_credit_hold = _bool(r.get("Customer_CreditHold")),
        guaranteed_christmas = _bool(r.get("OrderHed_GuaranteedChristmasDelivery_c")),
        display_order        = _bool(r.get("OrderHed_DisplayOrder_c")),
        order_date           = _date(r.get("OrderHed_OrderDate")),
        need_by_date         = _date(r.get("OrderRel_NeedByDate")),
        req_date             = _date(r.get("OrderRel_ReqDate")),
        original_ship_by     = _date(r.get("OrderRel_OriginalShipBy_c")),
        original_need_by     = _date(r.get("OrderRel_OriginalNeedBy_c")),
        order_received_date  = _date(r.get("OrderHed_OrderReceivedDate_c")),
        customer_delivery_requested = _date(r.get("OrderHed_CustomerDeliveryDateRequested_c")),
        last_xmas_order_date = _date(r.get("Customer_LastOrderReceivedDateGuaranteedChristmas_c")),
        last_xmas_delivery   = _date(r.get("Customer_LastChristmasDeliveryDate_c")),
        order_ack_sent       = _date(r.get("OrderHed_OrderAckSent_c")),
        customer_id      = r.get("Customer_CustID") or None,
        customer_name    = r.get("Customer_Name") or None,
        customer_country = r.get("Customer_Country") or None,
        customer_group   = r.get("Customer_GroupCode") or None,
        ship_to_num      = r.get("OrderHed_ShipToNum") or None,
        sales_rep        = r.get("OrderHed_SalesRepList") or None,
        sur_name         = r.get("OrderHed_SurName_c") or None,
        so_type          = r.get("OrderHed_SOType_c") or None,
        so_type_desc     = r.get("UDCodes_CodeDesc") or None,
        channel          = r.get("ICCode_Description") or None,
        prod_code        = r.get("OrderDtl_ProdCode") or None,
        entry_person     = r.get("OrderHed_EntryPerson") or None,
        created_by       = r.get("OrderDtl_CreatedBy_c") or None,
        job_num          = r.get("JobProd_JobNum") or None,
        job_released     = _bool(r.get("JobHead_JobReleased")),
        job_firm         = _bool(r.get("JobHead_JobFirm")),
        prod_plnwk       = str(r["JobHead_ProdPlnWk_c"]) if r.get("JobHead_ProdPlnWk_c") else None,
        assembly_seq     = r.get("JobAsmbl_AssemblySeq"),
        part_num         = r.get("Calculated_PartNum") or None,
        part_desc        = r.get("Calculated_PartDesc") or None,
        base_part_num    = r.get("OrderDtl_BasePartNum") or None,
        xpart_num        = r.get("OrderDtl_XPartNum") or None,
        ium              = r.get("OrderDtl_IUM") or None,
        wip_bin          = r.get("PartWip_BinNum") or None,
        model            = r.get("Calculated_Model") or None,
        size_desc        = r.get("Calculated_SizeDesc") or None,
        prod_size        = r.get("Calculated_ProdSize") or None,
        cover            = r.get("Calculated_Cover") or None,
        cover_desc       = r.get("Calculated_CoverDesc") or None,
        leg              = r.get("Calculated_Leg") or None,
        leg_mtl          = r.get("Calculated_LegMtl") or None,
        castor_mtl       = r.get("Calculated_CastorMtl") or None,
        stud1_mtl        = r.get("Calculated_Stud1Mtl") or None,
        stud2_mtl        = r.get("Calculated_Stud2Mtl") or None,
        seat_interior_mtl = r.get("Calculated_SeatInteriorMtl") or None,
        back_interior_mtl = r.get("Calculated_BackInteriorMtl") or None,
        scat_interior_mtl = r.get("Calculated_ScatInteriorMtl") or None,
        material_1       = r.get("Calculated_Material1") or None,
        material_1_desc  = r.get("Calculated_Cover1PartDesc") or None,
        material_2       = r.get("Calculated_Material2") or None,
        material_2_desc  = r.get("Calculated_Cover2PartDesc") or None,
        material_3       = r.get("Calculated_Material3") or None,
        material_3_desc  = r.get("Calculated_Cover3PartDesc") or None,
        material_4       = r.get("Calculated_Material4") or None,
        material_4_desc  = r.get("Calculated_Cover4PartDesc") or None,
        material_5       = r.get("Calculated_Material5") or None,
        material_5_desc  = r.get("Calculated_Cover5PartDesc") or None,
        material_6       = r.get("Calculated_Material6") or None,
        material_6_desc  = r.get("Calculated_Cover6PartDesc") or None,
        material_7       = r.get("Calculated_Material7") or None,
        material_7_desc  = r.get("Calculated_Cover7PartDesc") or None,
        material_8       = r.get("Calculated_Material8") or None,
        material_8_desc  = r.get("Calculated_Cover8PartDesc") or None,
        selling_qty      = _dec(r.get("Calculated_SellingQty")),
        shipped_qty      = _dec(r.get("Calculated_ShippedQty")),
        required_qty     = _dec(r.get("JobAsmbl_RequiredQty")),
        qty_completed    = _dec(r.get("JobHead_QtyCompleted")),
        release_qty      = _dec(r.get("Calculated_ReleaseQty")),
        release_price    = _dec(r.get("Calculated_ReleasePrice")),
        release_price_gbp= _dec(r.get("Calculated_ReleasePriceGBP")),
        currency_code    = r.get("OrderHed_CurrencyCode") or None,
        exchange_rate    = _dec(r.get("OrderHed_ExchangeRate")),
        order_book_comments   = r.get("OrderHed_OrderBookComments_c") or None,
        ship_by_changed_count = r.get("OrderRel_ShipByChangedCount_c"),
        need_by_changed_count = r.get("OrderRel_NeedByChangedCount_c"),
        imported_at      = now,
    )


class SalesOrderOpenImporter(EpicorBaqImporter):
    """
    Daily snapshot of all currently open sales orders.

    Strategy: delete all rows where open_order=True, then INSERT the fresh
    snapshot.  This keeps closed historical orders untouched.
    """

    BAQ_NAME    = "bskyVA05v1"
    IMPORT_TYPE = "epicor_sales_open"
    BAQ_PARAMS  = {"OpenOrders": "Open"}
    PAGE_SIZE   = 10000   # BAQ ignores $top — returns full set in one response

    def _target_table(self) -> str:
        return "sales_orders"

    def _sync_records(self, records: list[dict], batch: ImportBatch, now: datetime) -> None:
        from datetime import datetime as _dt
        from decimal import Decimal, InvalidOperation
        from app.extensions import db
        from app.sales.orders.models import SalesOrder

        def _dec(v):
            if v is None or v == "": return None
            try: return Decimal(str(v))
            except InvalidOperation: return None

        def _date(v):
            if not v: return None
            try: return _dt.fromisoformat(v.replace("Z", "+00:00")).date()
            except (ValueError, AttributeError): return None

        def _bool(v):
            if isinstance(v, bool): return v
            if isinstance(v, (int, float)): return bool(v)
            if isinstance(v, str): return v.strip().lower() in ("true", "1", "yes")
            return None

        # Delete the current open-order snapshot, preserve closed history
        deleted = SalesOrder.query.filter(SalesOrder.open_order == True).delete()
        db.session.flush()

        seen: set = set()
        skipped = 0
        for r in records:
            key = (
                r.get("OrderHed_OrderNum"),
                r.get("OrderDtl_OrderLine"),
                r.get("OrderRel_OrderRelNum"),
                r.get("JobAsmbl_AssemblySeq"),
                r.get("JobProd_JobNum"),
            )
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            db.session.add(_build_sales_order(r, _date, _dec, _bool, now))

        batch.rows_inserted = len(records) - skipped
        batch.notes = f"Deleted {deleted} stale open rows; {skipped} duplicates skipped"


class SalesOrderClosedImporter(EpicorBaqImporter):
    """
    On-demand import of closed sales orders for a date range.

    Default date range: 1 Jan of current year → today.
    Override from the admin UI or CLI:
        flask epicor sync sales_closed \\
          -p OrderDateFrom=01/01/2025 -p OrderDateTo=31/12/2025

    Strategy: delete existing records whose order_num appears in the batch,
    then INSERT the fetched records.  Safe to re-run for the same range.
    """

    BAQ_NAME    = "bskyVA05v1"
    IMPORT_TYPE = "epicor_sales_closed"
    BAQ_PARAMS  = {"OpenOrders": "Closed"}
    PAGE_SIZE   = 10000

    # Incremental — a narrow date window may legitimately return 0 rows.
    ALLOW_EMPTY_RESULT = True

    # How many days before the last successful run to re-fetch, so that
    # corrections / amendments to recently-closed orders are picked up.
    _OVERLAP_DAYS = 30

    # Hard cap on how far back the open-order floor can extend the query window.
    # Prevents the BAQ from being asked for multiple years of closed orders on
    # every daily run.  Orders with order_dates older than this cap will not be
    # caught automatically if they close — use a manual backfill for those.
    # Increase if your order cycles routinely exceed 12 months.
    MAX_LOOKBACK_DAYS = 365

    def _target_table(self) -> str:
        return "sales_orders"

    def get_dynamic_params(self) -> dict:
        """
        Rolling-window incremental with open-order floor.

        The date range is the EARLIER of:
          • (last_run − OVERLAP_DAYS) — rolling 30-day overlap to catch corrections
          • earliest open order_date currently in the DB — ensures orders that
            transition from open → closed are never missed, regardless of how
            old their order_date is (e.g. overdue 2025 orders that close in 2026)

        First run (no previous successful batch): fetches 1 Jan of current year
        → today as an initial baseline.  To seed further back, call:
            flask epicor sync sales_closed -p OrderDateFrom=01/01/2024
        """
        from datetime import date, timedelta
        from app.extensions import db
        from app.sales.orders.models import ImportBatch as IB, SalesOrder

        today = date.today()
        last_batch = (
            IB.query
            .filter_by(import_type=self.IMPORT_TYPE, status=IB.STATUS_SUCCESS)
            .order_by(IB.uploaded_at.desc())
            .first()
        )

        if last_batch:
            rolling_from = last_batch.uploaded_at.date() - timedelta(days=self._OVERLAP_DAYS)
        else:
            # First ever run — load current year as the baseline.
            rolling_from = date(today.year, 1, 1)

        # Extend the window back to cover any currently-open order that might
        # have since closed — their order_date could be in a prior year.
        # Capped at MAX_LOOKBACK_DAYS to prevent expensive multi-year BAQ queries.
        cap_date = today - timedelta(days=self.MAX_LOOKBACK_DAYS)
        earliest_open = (
            db.session.query(db.func.min(SalesOrder.order_date))
            .filter(SalesOrder.open_order == True, SalesOrder.order_date.isnot(None))
            .scalar()
        )
        if earliest_open and earliest_open < rolling_from:
            from_d = max(earliest_open, cap_date)
        else:
            from_d = rolling_from

        return {
            "OrderDateFrom": from_d.strftime("%d/%m/%Y"),
            "OrderDateTo":   today.strftime("%d/%m/%Y"),
        }

    def _sync_records(self, records: list[dict], batch: ImportBatch, now: datetime) -> None:
        from datetime import datetime as _dt
        from decimal import Decimal, InvalidOperation
        from app.extensions import db
        from app.sales.orders.models import SalesOrder

        def _dec(v):
            if v is None or v == "": return None
            try: return Decimal(str(v))
            except InvalidOperation: return None

        def _date(v):
            if not v: return None
            try: return _dt.fromisoformat(v.replace("Z", "+00:00")).date()
            except (ValueError, AttributeError): return None

        def _bool(v):
            if isinstance(v, bool): return v
            if isinstance(v, (int, float)): return bool(v)
            if isinstance(v, str): return v.strip().lower() in ("true", "1", "yes")
            return None

        # Delete any existing records for the order numbers in this batch
        # so a re-run of the same date range is idempotent
        order_nums = {r["OrderHed_OrderNum"] for r in records if r.get("OrderHed_OrderNum")}
        if order_nums:
            SalesOrder.query.filter(SalesOrder.order_num.in_(order_nums)).delete(
                synchronize_session=False
            )
            db.session.flush()

        seen: set = set()
        skipped = 0
        for r in records:
            key = (
                r.get("OrderHed_OrderNum"),
                r.get("OrderDtl_OrderLine"),
                r.get("OrderRel_OrderRelNum"),
                r.get("JobAsmbl_AssemblySeq"),
                r.get("JobProd_JobNum"),
            )
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            db.session.add(_build_sales_order(r, _date, _dec, _bool, now))

        batch.rows_inserted = len(records) - skipped
        if skipped:
            batch.notes = f"{skipped} duplicate rows skipped"


# ---------------------------------------------------------------------------
# Production Output   →   BAQ: PlanningOutPut
# ---------------------------------------------------------------------------

class ProductionOutputImporter(EpicorBaqImporter):
    """
    Daily production/labour output from the PlanningOutPut BAQ.

    Synced by date range (default: 01 Jan current year → today).
    Strategy: DELETE rows where clock_in_date in the range, then INSERT.

    Override from the admin UI or CLI:
        flask epicor sync production_output -p DateFrom=2026-01-01 -p DateTo=2026-07-04
    """

    BAQ_NAME    = "PlanningOutPut"
    IMPORT_TYPE = "epicor_production_output"
    BAQ_PARAMS  = {}

    # Incremental — a date window with no new output legitimately returns 0 rows.
    ALLOW_EMPTY_RESULT = True

    # Earliest date to fetch on the very first run (full-history load).
    # Adjust if you need to go further back.
    HISTORY_START = "2020-01-01"

    def _target_table(self) -> str:
        return "production_output"

    def get_dynamic_params(self) -> dict:
        """
        Incremental: fetch only dates not yet in the database.

        Queries the maximum ``clock_in_date`` already stored and uses the
        following day as ``DateFrom``.  On the very first run (empty table)
        falls back to ``HISTORY_START`` for a full-history load.
        """
        from datetime import date, timedelta
        from app.extensions import db
        from app.operations.models import ProductionOutput

        # Maximum window per incremental sync run.  Large windows cause the
        # BAQ to paginate deeply and can exceed the web-server worker timeout.
        # Backfill by running the sync multiple times; each run advances the
        # window forward by up to MAX_DAYS_PER_RUN days.
        MAX_DAYS_PER_RUN = 90

        today = date.today()
        last_date = db.session.query(
            db.func.max(ProductionOutput.clock_in_date)
        ).scalar()

        if last_date:
            from_d = last_date + timedelta(days=1)
            # Nothing new to fetch — request today anyway; 0 rows is fine.
            if from_d > today:
                from_d = today
        else:
            # First ever run — load full history.
            from_d = date.fromisoformat(self.HISTORY_START)

        # Cap the window so a large gap never produces a single huge request.
        max_to = from_d + timedelta(days=MAX_DAYS_PER_RUN - 1)
        to_d = min(today, max_to)

        return {
            "DateFrom": from_d.isoformat(),
            "DateTo":   to_d.isoformat(),
        }

    def _sync_records(self, records: list[dict], batch: ImportBatch, now: datetime) -> None:
        from datetime import datetime as _dt
        from decimal import Decimal, InvalidOperation
        from app.extensions import db
        from app.operations.models import ProductionOutput

        def _dec(v):
            if v is None or v == "": return None
            try: return Decimal(str(v))
            except InvalidOperation: return None

        def _date(v):
            if not v: return None
            try: return _dt.fromisoformat(v.replace("Z", "+00:00")).date()
            except (ValueError, AttributeError): return None

        # Delete the full *requested* date range, not just the dates that
        # happened to appear in the returned records.  This prevents stale rows
        # surviving for days in the range that have no production (e.g. weekends).
        params = getattr(self, '_last_merged_params', {})
        req_from_str = params.get('DateFrom')
        req_to_str   = params.get('DateTo')
        deleted = 0
        if req_from_str and req_to_str:
            try:
                from_del = _dt.fromisoformat(req_from_str).date()
                to_del   = _dt.fromisoformat(req_to_str).date()
            except ValueError:
                from_del = to_del = None
        else:
            from_del = to_del = None

        if from_del is None:
            # Fallback: derive range from returned records
            dates = {_date(r.get("LaborDtl_ClockInDate")) for r in records}
            dates.discard(None)
            if dates:
                from_del, to_del = min(dates), max(dates)

        if from_del is not None and to_del is not None:
            deleted = ProductionOutput.query.filter(
                ProductionOutput.clock_in_date >= from_del,
                ProductionOutput.clock_in_date <= to_del,
            ).delete()
            db.session.flush()

        inserted = 0
        for r in records:
            db.session.add(ProductionOutput(
                row_ident    = r.get("RowIdent") or None,
                job_num      = r.get("LaborDtl_JobNum") or None,
                assembly_seq = r.get("JobAsmbl_AssemblySeq"),
                opr_seq      = r.get("LaborDtl_OprSeq"),
                op_desc      = r.get("JobOper_OpDesc") or None,
                employee_num = r.get("LaborDtl_EmployeeNum") or None,
                labor_entry_method = r.get("JobOper_LaborEntryMethod") or None,
                clock_in_date= _date(r.get("LaborDtl_ClockInDate")),
                labor_qty    = _dec(r.get("LaborDtl_LaborQty")),
                prod_plnwk   = str(r["JobHead_ProdPlnWk_c"]) if r.get("JobHead_ProdPlnWk_c") else None,
                model        = r.get("OrderDtl_Cnfg_Model_c") or None,
                mod_size     = r.get("Calculated_ModSize") or None,
                line_desc    = r.get("OrderDtl_LineDesc") or None,
                assembly_desc= r.get("JobAsmbl_Description") or None,
                currency_code    = r.get("OrderHed_CurrencyCode") or None,
                exchange_rate    = _dec(r.get("OrderHed_ExchangeRate")),
                release_price    = _dec(r.get("Calculated_ReleaseExtendedPrice")),
                release_discount = _dec(r.get("Calculated_ReleaseDiscountAmount")),
                misc_charges     = _dec(r.get("Calculated_ReleaseMiscCharges")),
                release_total    = _dec(r.get("Calculated_ReleaseTotal")),
                release_total_gbp= _dec(r.get("Calculated_ReleaseTotalGBP")),
                imported_at  = now,
            ))
            inserted += 1

        batch.rows_inserted = inserted
        batch.notes = f"Deleted {deleted} stale rows in date range; {inserted} inserted"


# ---------------------------------------------------------------------------
# Registry — single source of truth
# ---------------------------------------------------------------------------

#: Maps a short CLI-friendly key to its importer class.
#: Add new entries here as more BAQs are onboarded.
REGISTRY: dict[str, type[EpicorBaqImporter]] = {
    "stock":                 StockImporter,
    "purchase_orders":       PurchaseOrderImporter,
    "material_requirements": MaterialRequirementsImporter,
    "works_orders":          WorksOrderImporter,
    "sales_open":            SalesOrderOpenImporter,
    "sales_closed":          SalesOrderClosedImporter,
    "production_output":     ProductionOutputImporter,
}


def run_batch(
    client,
    keys: list[str] | None = None,
    triggered_by_id: int | None = None,
) -> dict[str, ImportBatch | Exception]:
    """
    Run one or more BAQ importers sharing a single KineticClient session.

    Args:
        client:           An open ``KineticClient`` instance.
        keys:             Subset of ``REGISTRY`` keys to run. ``None`` = run all.
        triggered_by_id:  ``User.id`` recorded on the ``ImportBatch`` audit log.

    Returns:
        ``{key: ImportBatch}`` for successes, ``{key: Exception}`` for failures.
        A failed importer does NOT abort the rest of the batch — all run.
    """
    targets = {k: REGISTRY[k] for k in (keys or REGISTRY)}
    results: dict = {}

    for key, importer_cls in targets.items():
        try:
            batch = importer_cls(client).run(triggered_by_id=triggered_by_id)
            results[key] = batch
        except Exception as exc:
            results[key] = exc

    return results