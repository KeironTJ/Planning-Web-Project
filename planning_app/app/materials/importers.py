"""
Materials CSV importers — all full replace (truncate + reload daily).

Covers:
- StockImporter            — StockOnHand_HIDE.csv
- OpenPoImporter           — OpenPO_HIDE.csv
- MainMaterialImporter     — MainMaterialReq_HIDE.csv
- AsMaterialImporter       — ASMaterialReq_HIDE.csv
"""

from datetime import datetime, timezone

from app.extensions import db
from app.core.csv_utils import read_csv_rows, excel_serial_to_date, parse_decimal, parse_int
from app.orders.models import ImportBatch
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
    """Import StockOnHand_HIDE.csv — full replace."""

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = _start_batch(ImportBatch.TYPE_STOCK, filename or "StockOnHand_HIDE.csv")
        batch.uploaded_by_id = uploaded_by_id
        now = datetime.now(timezone.utc)
        rows_inserted = 0

        try:
            all_rows = list(read_csv_rows(source))
            batch.row_count = len(all_rows)

            Stock.query.delete()
            db.session.flush()

            for row in all_rows:
                product_code = row.get("PRODCODE", "").strip()
                if not product_code:
                    continue
                s = Stock(
                    product_code=product_code,
                    description=row.get("DESCRIPTION") or None,
                    qty_on_hand=parse_decimal(row.get("STKQTY"), default=0),
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

    Note: the ERP exports the column as 'OUSTANDINGQTY' (typo) — matched exactly.
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
                po_number = row.get("POPNO", "").strip()
                if not po_number:
                    continue
                po = PurchaseOrder(
                    po_number=po_number,
                    line_number=parse_int(row.get("ORDITEM"), default=0),
                    product_code=row.get("PRODCODE") or None,
                    description=row.get("PLDESCRIPTION") or None,
                    outstanding_qty=parse_decimal(row.get("OUSTANDINGQTY")),  # ERP typo — exact match
                    due_date=excel_serial_to_date(row.get("DUEDATE")),
                    supplier_code=row.get("SUPPLIER") or None,
                    supplier_name=row.get("NAME") or None,
                    po_type=row.get("Type") or None,
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
    """Import MainMaterialReq_HIDE.csv — full replace."""

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = _start_batch(ImportBatch.TYPE_MAIN_MATERIAL, filename or "MainMaterialReq_HIDE.csv")
        batch.uploaded_by_id = uploaded_by_id
        now = datetime.now(timezone.utc)
        rows_inserted = 0

        try:
            all_rows = list(read_csv_rows(source))
            batch.row_count = len(all_rows)

            MaterialRequirementMain.query.delete()
            db.session.flush()

            for row in all_rows:
                material_code = row.get("MATERIALCODE", "").strip()
                if not material_code:
                    continue
                m = MaterialRequirementMain(
                    customer_id=row.get("CUSTID") or None,
                    batch_id=row.get("BATCHID") or None,
                    works_order=row.get("WORKSORDER") or None,
                    load_date=excel_serial_to_date(row.get("LOADDATE")),
                    due_date=excel_serial_to_date(row.get("DUEDATE")),
                    department=row.get("SECTIONDESC") or None,
                    material_code=material_code,
                    material_description=row.get("MATERIALDESC") or None,
                    qty_required_per_set=parse_decimal(row.get("QTYREQUIREDFORSETS")),
                    qty_for_order=parse_decimal(row.get("QTYFORORDER")),
                    qty_issued=parse_decimal(row.get("QTYISSUED"), default=0),
                    product_group=row.get("PRODGRP") or None,
                    product_group_desc=row.get("PGDESCRIPTION") or None,
                    complete=row.get("COMPLETE") or None,
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
