"""
Materials CSV importers — all full replace (truncate + reload daily).

Covers:
- StockImporter          — SOH_HIDE.csv            (Part, TotalOnHand, ...)
- OpenPoImporter         — OpenPO_HIDE.csv          (PO, Line, Part Num, ...)
- MainMaterialImporter   — MatReq_HIDE.csv          (Material, RequiredQty, ...)
"""

from datetime import datetime, timezone

from app.extensions import db
from app.core.csv_utils import read_csv_rows, excel_serial_to_date, parse_decimal, parse_int
from app.sales.orders.models import ImportBatch
from .models import Stock, PurchaseOrder, MaterialRequirementMain


def _start_batch(import_type, filename):
    batch = ImportBatch(
        import_type=import_type,
        filename=filename,
        status=ImportBatch.STATUS_PENDING,
    )
    db.session.add(batch)
    db.session.flush()
    return batch


def _fail_batch(batch, exc):
    db.session.rollback()
    batch.status = ImportBatch.STATUS_FAILED
    batch.error_message = str(exc)
    try:
        db.session.add(batch)
        db.session.commit()
    except Exception:
        db.session.rollback()
    raise


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------

class StockImporter:
    """
    Import SOH_HIDE.csv — full replace.

    Column mapping (Epicor Kinetic export):
        Part          → product_code
        Description   → description
        TotalOnHand   → qty_on_hand
    """

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = _start_batch(ImportBatch.TYPE_STOCK, filename or "SOH_HIDE.csv")
        batch.uploaded_by_id = uploaded_by_id
        now = datetime.now(timezone.utc)
        rows_inserted = 0

        try:
            all_rows = list(read_csv_rows(source))
            batch.row_count = len(all_rows)

            Stock.query.delete()
            db.session.flush()

            for row in all_rows:
                product_code = row.get("Part", "").strip()
                if not product_code:
                    continue
                s = Stock(
                    product_code=product_code,
                    description=row.get("Description") or None,
                    qty_on_hand=parse_decimal(row.get("TotalOnHand"), default=0),
                    imported_at=now,
                )
                db.session.add(s)
                rows_inserted += 1

            batch.rows_inserted = rows_inserted
            batch.status = ImportBatch.STATUS_SUCCESS
            db.session.commit()

        except Exception as exc:
            _fail_batch(batch, exc)

        return batch


# ---------------------------------------------------------------------------
# Open Purchase Orders
# ---------------------------------------------------------------------------

class OpenPoImporter:
    """
    Import OpenPO_HIDE.csv — full replace.

    Column mapping (Epicor Kinetic export):
        PO           → po_number
        Line         → line_number
        Part Num     → product_code
        Description  → description
        OutstandingQty → outstanding_qty
        Due Date     → due_date  (Excel serial; may be blank)
        Supplier ID  → supplier_code
        Name         → supplier_name
    Note: no CO/PO type column in this export; all rows treated as type PO.
    """

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = _start_batch(ImportBatch.TYPE_OPEN_PO, filename or "OpenPO_HIDE.csv")
        batch.uploaded_by_id = uploaded_by_id
        now = datetime.now(timezone.utc)
        rows_inserted = 0

        try:
            all_rows = list(read_csv_rows(source))
            batch.row_count = len(all_rows)

            PurchaseOrder.query.delete()
            db.session.flush()

            for row in all_rows:
                po_number = row.get("PO", "").strip()
                if not po_number:
                    continue
                po = PurchaseOrder(
                    po_number=po_number,
                    line_number=parse_int(row.get("Line"), default=0),
                    product_code=row.get("Part Num") or None,
                    description=row.get("Description") or None,
                    outstanding_qty=parse_decimal(row.get("OutstandingQty")),
                    due_date=excel_serial_to_date(row.get("Due Date")),
                    supplier_code=row.get("Supplier ID") or None,
                    supplier_name=row.get("Name") or None,
                    po_type="PO",
                    imported_at=now,
                )
                db.session.add(po)
                rows_inserted += 1

            batch.rows_inserted = rows_inserted
            batch.status = ImportBatch.STATUS_SUCCESS
            db.session.commit()

        except Exception as exc:
            _fail_batch(batch, exc)

        return batch


# ---------------------------------------------------------------------------
# Main Material Requirements
# ---------------------------------------------------------------------------

class MainMaterialImporter:
    """
    Import MatReq_HIDE.csv — full replace.

    Column mapping (Epicor Kinetic export):
        Order         → so_number    (direct SO number for pegging)
        JobNum        → works_order
        Material      → material_code
        Description   → material_description
        RequiredQty   → qty_for_order
        Issued Qty    → qty_issued
        IssuedComplete→ complete (TRUE→'Y', FALSE→'N')
        Req. By       → due_date  (Excel serial)
        QtyPer        → qty_required_per_set
        Warehouse     → department
        Site          → (site identifier, used if multi-site mapping configured)

    Rows where Closed=TRUE are skipped (job already closed in ERP).
    """

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = _start_batch(ImportBatch.TYPE_MAIN_MATERIAL, filename or "MatReq_HIDE.csv")
        batch.uploaded_by_id = uploaded_by_id
        now = datetime.now(timezone.utc)
        rows_inserted = 0

        try:
            all_rows = list(read_csv_rows(source))
            batch.row_count = len(all_rows)

            MaterialRequirementMain.query.delete()
            db.session.flush()

            for row in all_rows:
                # Skip closed jobs — they have no open requirements
                if row.get("Closed", "").strip().upper() == "TRUE":
                    continue

                material_code = row.get("Material", "").strip()
                if not material_code:
                    continue

                is_issued_complete = row.get("IssuedComplete", "").strip().upper() == "TRUE"

                m = MaterialRequirementMain(
                    so_number=row.get("Order", "").strip() or None,
                    works_order=row.get("JobNum") or None,
                    due_date=excel_serial_to_date(row.get("Req. By")),
                    department=row.get("Warehouse") or None,
                    material_code=material_code,
                    material_description=row.get("Description") or None,
                    qty_required_per_set=parse_decimal(row.get("QtyPer")),
                    qty_for_order=parse_decimal(row.get("RequiredQty")),
                    qty_issued=parse_decimal(row.get("Issued Qty"), default=0),
                    complete="Y" if is_issued_complete else "N",
                    imported_at=now,
                )
                db.session.add(m)
                rows_inserted += 1

            batch.rows_inserted = rows_inserted
            batch.status = ImportBatch.STATUS_SUCCESS
            db.session.commit()

        except Exception as exc:
            _fail_batch(batch, exc)

        return batch
