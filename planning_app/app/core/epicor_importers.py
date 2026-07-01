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
from app.orders.models import ImportBatch


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
        from app.materials.models import Stock

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
        from app.materials.models import PurchaseOrder

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
        from app.materials.models import MaterialRequirementMain

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

        seen: set = set()
        skipped = 0
        for r in records:
            key = (
                r.get("JobHead_JobNum"),
                r.get("JobAsmbl_AssemblySeq"),
                r.get("JobMtl_MtlSeq"),
                r.get("OrderHed_OrderNum"),
            )
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
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

        batch.rows_inserted = len(records) - skipped
        if skipped:
            batch.notes = f"{skipped} duplicate BAQ rows skipped"


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
    BAQ_PARAMS = {}

    def _sync_records(
        self, records: list[dict], batch: ImportBatch, now: datetime
    ) -> None:
        # TODO: implement once the WorksOrder / WorksOrderOperation model is created.
        # Run `flask epicor inspect works_orders` to discover BAQ fields.
        raise NotImplementedError(
            "WorksOrderImporter._sync_records() is not yet implemented. "
            "Run `flask epicor inspect works_orders` to discover BAQ fields."
        )


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
