"""
Orders CSV importers.

Covers:
- OobImporter            â€” OpenOrderBook_HIDE.csv (legacy, UPSERT, preserves planner fields)
- SalesImporter          â€” sales_HIDE.csv (Epicor SALES export â†’ SalesOrderLine)
- CooisImporter          â€” COOIS_HIDE.csv (Epicor COOIS export â†’ WorksOrderOperation)
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.core.csv_utils import (
    read_csv_rows, excel_serial_to_date, parse_decimal, parse_int, parse_bool_yn,
)
from .models import (
    Department, SalesOrderLine, WorksOrderOperation,
    ImportBatch,
)

# Columns in the OOB that belong to the parent SalesOrderLine
_SOL_ERP_FIELDS = {
    "customer_code", "customer_name", "customer_order_ref", "customer_product_ref",
    "order_type",
    "product_code", "product_description", "qty_ordered",
    "order_date", "due_date", "unit_price", "total_value",
}



# ---------------------------------------------------------------------------
# OOB Importer
# ---------------------------------------------------------------------------

class OobImporter:
    """
    Import OpenOrderBook_HIDE.csv into sales_order_lines + works_order_operations.

    Strategy: UPSERT
    - Parent (SalesOrderLine): update ERP fields on match; insert on new.
    - Child (WorksOrderOperation): update ERP fields on match; insert on new.
    - Operations that were in a previous import but absent from today's CSV
      and are not completed/closed are marked as 'closed'.
    - Planner fields (status, planned_date, completed_date, notes) are NEVER
      touched by the importer on existing records.
    """

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        """
        Run the OOB import.

        Args:
            source: file path (str) or file-like stream
            uploaded_by_id: User.id of the uploader (nullable)
            filename: original filename for the audit log

        Returns:
            ImportBatch record with import results.
        """
        batch = ImportBatch(
            import_type=ImportBatch.TYPE_OOB,
            filename=filename or "OpenOrderBook_HIDE.csv",
            uploaded_by_id=uploaded_by_id,
            status=ImportBatch.STATUS_PENDING,
        )
        db.session.add(batch)
        db.session.flush()  # get batch.id without committing

        now = datetime.now(timezone.utc)
        rows_inserted = 0
        rows_updated = 0
        rows_closed = 0

        try:
            # --- 1. Read and group all rows by (SOPNO, ORDITEM) ---
            all_rows = list(read_csv_rows(source))
            batch.row_count = len(all_rows)

            # Group: {(so_number, line_number): [row, ...]}
            groups: dict[tuple, list] = {}
            for row in all_rows:
                key = (row.get("SOPNO", ""), row.get("ORDITEM", ""))
                groups.setdefault(key, []).append(row)

            # Pre-load department lookup: {name_lower: Department.id}
            dept_lookup: dict[str, int] = {
                d.name.lower(): d.id for d in Department.query.all()
            }

            # Track all (so_number, line_number, work_centre_name) keys seen in this import
            seen_op_keys: set[tuple] = set()

            # --- 2. Process each SO line group ---
            for (so_number, line_number_str), rows in groups.items():
                line_number = parse_int(line_number_str)
                if not so_number or line_number is None:
                    continue

                first = rows[0]

                # Build parent fields from the first row in the group
                order_date = excel_serial_to_date(first.get("ORDDATE"))
                due_date = excel_serial_to_date(first.get("DUEDATE"))

                sol_data = {
                    "customer_code": first.get("CUSTOMER") or None,
                    "customer_name": first.get("NAME") or None,
                    "customer_order_ref": first.get("CUSTORDREF") or None,
                    "customer_product_ref": first.get("CUSPRODREF") or None,
                    "order_type": first.get("ORDERTYPE") or None,
                    "product_code": first.get("PRODCODE") or None,
                    "product_description": first.get("DESCRIPTION") or None,
                    "qty_ordered": parse_decimal(first.get("QTY")),
                    "order_date": order_date,
                    "due_date": due_date,
                    "unit_price": parse_decimal(first.get("SELLPRICE")),
                    "total_value": parse_decimal(first.get("TOTALVALUE")),
                    "imported_at": now,
                }

                # UPSERT SalesOrderLine
                sol = SalesOrderLine.query.filter_by(
                    so_number=so_number, line_number=line_number
                ).first()

                if sol:
                    for k, v in sol_data.items():
                        setattr(sol, k, v)
                else:
                    sol = SalesOrderLine(so_number=so_number, line_number=line_number, **sol_data)
                    db.session.add(sol)
                    db.session.flush()  # get sol.id

                # --- 3. Process each operation row (one per work centre) ---
                for row in rows:
                    wc_name = row.get("WORKCENTRE", "").strip()
                    if not wc_name:
                        continue

                    op_key = (so_number, line_number, wc_name)
                    seen_op_keys.add(op_key)

                    dept_id = dept_lookup.get(wc_name.lower())
                    op_due_date = excel_serial_to_date(row.get("DUEDATE"))
                    op_qty = parse_decimal(row.get("QTY"))
                    op_total_value = parse_decimal(row.get("TOTALVALUE"))

                    op = WorksOrderOperation.query.filter_by(
                        so_number=so_number,
                        line_number=line_number,
                        work_centre_name=wc_name,
                    ).first()

                    if op:
                        # Update ERP fields only â€” never touch planner fields
                        op.qty = op_qty
                        op.due_date = op_due_date
                        op.total_value = op_total_value
                        op.department_id = dept_id
                        op.imported_at = now
                        rows_updated += 1
                    else:
                        op = WorksOrderOperation(
                            sales_order_line_id=sol.id,
                            department_id=dept_id,
                            so_number=so_number,
                            line_number=line_number,
                            work_centre_name=wc_name,
                            qty=op_qty,
                            due_date=op_due_date,
                            total_value=op_total_value,
                            status=WorksOrderOperation.STATUS_NEW_ORDER,
                            imported_at=now,
                        )
                        db.session.add(op)
                        rows_inserted += 1

            # --- 4. Mark absent operations as closed ---
            # OOB is the authoritative source â€” if absent, the order has shipped/cancelled in ERP.
            # Close everything that isn't already in a terminal state.
            terminal_statuses = [
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ]
            open_ops = WorksOrderOperation.query.filter(
                WorksOrderOperation.status.notin_(terminal_statuses)
            ).all()

            _terminal = {WorksOrderOperation.STATUS_COMPLETED, WorksOrderOperation.STATUS_CLOSED}
            today = date.today()

            closed_sol_keys: set[tuple] = set()
            for op in open_ops:
                key = (op.so_number, op.line_number, op.work_centre_name)
                if key not in seen_op_keys:
                    op.status = WorksOrderOperation.STATUS_CLOSED
                    if op.completed_date is None:
                        op.completed_date = today
                    rows_closed += 1
                    closed_sol_keys.add((op.so_number, op.line_number))

            # --- 5. Finalise batch ---
            batch.rows_inserted = rows_inserted
            batch.rows_updated = rows_updated
            batch.rows_closed = rows_closed
            batch.status = ImportBatch.STATUS_SUCCESS
            db.session.commit()

        except Exception as exc:
            db.session.rollback()
            batch.status = ImportBatch.STATUS_FAILED
            batch.error_message = str(exc)
            try:
                db.session.add(batch)
                db.session.commit()
            except Exception:
                db.session.rollback()
            raise

        return batch


# ---------------------------------------------------------------------------
# Sales Importer  (sales_HIDE.csv â†’ SalesOrderLine)
# ---------------------------------------------------------------------------



class SalesImporter:
    """
    Import sales_HIDE.csv into sales_order_lines.

    Strategy: UPSERT on (so_number, line_number).
    Only rows with AsmSeq=0 are processed (main assemblies, not sub-assemblies/scatters).
    Voided orders are skipped.
    ERP fields are updated on every import; planner fields are never touched.

    Column mapping:
        Order           â†’ so_number
        Line            â†’ line_number
        CustID          â†’ customer_code
        Customer Name   â†’ customer_name
        Cust PONum      â†’ customer_order_ref
        SOType_c        â†’ order_type
        PartNum         â†’ product_code
        PartDesc        â†’ product_description
        Selling Qty     â†’ qty_ordered
        Order Date      â†’ order_date  (Excel serial)
        Ship By         â†’ due_date    (Excel serial)
        ReleasePriceGBP â†’ unit_price
    """

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = ImportBatch(
            import_type=ImportBatch.TYPE_SALES,
            filename=filename or "sales_HIDE.csv",
            uploaded_by_id=uploaded_by_id,
            status=ImportBatch.STATUS_PENDING,
        )
        db.session.add(batch)
        db.session.flush()

        now = datetime.now(timezone.utc)
        rows_inserted = 0
        rows_updated = 0

        try:
            all_rows = list(read_csv_rows(source))
            batch.row_count = len(all_rows)

            # Only main assemblies (AsmSeq=0); skip voided orders
            # Deduplicate on (Order, Line) â€” first AsmSeq=0 row wins for parent data
            parent_rows: dict[tuple, dict] = {}
            for row in all_rows:
                if str(row.get("AsmSeq", "0")).strip() != "0":
                    continue
                if str(row.get("Void", "FALSE")).strip().upper() == "TRUE":
                    continue
                key = (str(row.get("Order", "")).strip(), str(row.get("Line", "")).strip())
                if key[0] and key[1] and key not in parent_rows:
                    parent_rows[key] = row

            for (so_number, line_number_str), row in parent_rows.items():
                line_number = parse_int(line_number_str)
                if line_number is None:
                    continue

                selling_qty = parse_decimal(row.get("Selling Qty"))
                unit_price = parse_decimal(row.get("ReleasePriceGBP"))
                total_value = (
                    (selling_qty or Decimal(0)) * (unit_price or Decimal(0))
                    if selling_qty and unit_price else None
                )

                sol_data = {
                    "customer_code":        row.get("CustID") or None,
                    "customer_name":        row.get("Customer Name") or None,
                    "customer_order_ref":   row.get("Cust PONum") or None,
                    "order_type":           row.get("SOType_c") or None,
                    "product_code":         row.get("PartNum") or None,
                    "product_description":  row.get("PartDesc") or None,
                    "qty_ordered":          selling_qty,
                    "order_date":           excel_serial_to_date(row.get("Order Date")),
                    "due_date":             excel_serial_to_date(row.get("Ship By")),
                    "unit_price":           unit_price,
                    "total_value":          total_value,
                    "model":                row.get("Model") or None,
                    "product_size":         (row.get("ProdSize") or "").strip() or None,
                    "product_group":        row.get("Product Group") or None,
                    "customer_group":       row.get("Customer Group") or None,
                    "channel":              row.get("IC Description") or None,
                    "country":              row.get("Country") or None,
                    "is_open":              str(row.get("Open Line", "TRUE")).strip().upper() == "TRUE",
                    "imported_at":          now,
                }

                sol = SalesOrderLine.query.filter_by(
                    so_number=so_number, line_number=line_number
                ).first()
                if sol:
                    for k, v in sol_data.items():
                        setattr(sol, k, v)
                    rows_updated += 1
                else:
                    sol = SalesOrderLine(
                        so_number=so_number, line_number=line_number, **sol_data
                    )
                    db.session.add(sol)
                    rows_inserted += 1

            batch.rows_inserted = rows_inserted
            batch.rows_updated = rows_updated
            batch.status = ImportBatch.STATUS_SUCCESS
            db.session.commit()

        except Exception as exc:
            db.session.rollback()
            batch.status = ImportBatch.STATUS_FAILED
            batch.error_message = str(exc)
            try:
                db.session.add(batch)
                db.session.commit()
            except Exception:
                db.session.rollback()
            raise

        return batch


# ---------------------------------------------------------------------------
# COOIS Importer  (COOIS_HIDE.csv â†’ WorksOrderOperation)
# ---------------------------------------------------------------------------

class CooisImporter:
    """
    Import COOIS_HIDE.csv into works_order_operations.

    COOIS (Component Operations Information System) provides a snapshot of
    each job's current operation. One row per job assembly; AsmSeq=0 rows
    represent the main production jobs.

    Strategy: UPSERT on (so_number, line_number, work_centre_name).
    Operations absent from the current COOIS export and not already in a
    terminal state are marked as closed (job has shipped or been cancelled).

    Planner fields (planned_date, notes) are NEVER overwritten.
    Status is set on INSERT from ERP data; on UPDATE it is only changed to
    COMPLETED when the ERP reports Job Complete = TRUE.

    Column mapping:
        Order Num           â†’ so_number
        Line Num            â†’ line_number
        Current Op          â†’ work_centre_name
        Order Ship By       â†’ due_date    (Excel serial)
        Selling Qty         â†’ qty
        Firm                â†’ used for status derivation
        Released            â†’ used for status derivation
        Complete Qty        â†’ used for status derivation (> 0 = WIP)
        Job Complete        â†’ used for status derivation (TRUE = COMPLETED)

    If a SalesOrderLine for (so_number, line_number) does not already exist,
    a minimal one is created from COOIS data so the operation can be linked.
    """

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = ImportBatch(
            import_type=ImportBatch.TYPE_COOIS,
            filename=filename or "COOIS_HIDE.csv",
            uploaded_by_id=uploaded_by_id,
            status=ImportBatch.STATUS_PENDING,
        )
        db.session.add(batch)
        db.session.flush()

        now = datetime.now(timezone.utc)
        rows_inserted = 0
        rows_updated = 0
        rows_closed = 0

        try:
            all_rows = list(read_csv_rows(source))
            batch.row_count = len(all_rows)

            # Filter: main assemblies only (AsmSeq=0)
            main_rows = [
                r for r in all_rows
                if str(r.get("AsmSeq", "0")).strip() == "0"
            ]

            # Group by (Order Num, Line Num)
            groups: dict[tuple, list] = {}
            for row in main_rows:
                key = (
                    str(row.get("Order Num", "")).strip(),
                    str(row.get("Line Num", "")).strip(),
                )
                if key[0] and key[1]:
                    groups.setdefault(key, []).append(row)

            dept_lookup: dict[str, int] = {
                d.name.lower(): d.id for d in Department.query.all()
            }
            seen_op_keys: set[tuple] = set()

            for (so_number, line_number_str), rows in groups.items():
                line_number = parse_int(line_number_str)
                if line_number is None:
                    continue

                first = rows[0]
                due_date = excel_serial_to_date(first.get("Order Ship By"))
                unit_price = parse_decimal(first.get("Net Unit Price GBP"))
                selling_qty = parse_decimal(first.get("Selling Qty"))
                total_value = (
                    (selling_qty or Decimal(0)) * (unit_price or Decimal(0))
                    if selling_qty and unit_price else None
                )

                # Ensure parent SalesOrderLine exists
                sol = SalesOrderLine.query.filter_by(
                    so_number=so_number, line_number=line_number
                ).first()
                if not sol:
                    sol = SalesOrderLine(
                        so_number=so_number,
                        line_number=line_number,
                        customer_code=first.get("Customer ID") or None,
                        customer_name=first.get("Customer Name") or None,
                        order_type=first.get("SO Type") or None,
                        product_code=first.get("Asm Part Number") or None,
                        product_description=first.get("Asm Part Description") or None,
                        qty_ordered=selling_qty,
                        due_date=due_date,
                        unit_price=unit_price,
                        total_value=total_value,
                        imported_at=now,
                    )
                    db.session.add(sol)
                    db.session.flush()

                # Process each row as one WorksOrderOperation
                for row in rows:
                    wc_name = str(row.get("Current Op", "")).strip()
                    if not wc_name:
                        continue

                    op_key = (so_number, line_number, wc_name)
                    seen_op_keys.add(op_key)

                    dept_id = dept_lookup.get(wc_name.lower())
                    op_due_date = excel_serial_to_date(row.get("Order Ship By"))
                    op_qty = parse_decimal(row.get("Selling Qty"))

                    # Derive ERP status from flags
                    is_complete = str(row.get("Job Complete", "")).strip().upper() == "TRUE"
                    is_released = str(row.get("Released", "")).strip().upper() == "TRUE"
                    is_firm     = str(row.get("Firm", "")).strip().upper() == "TRUE"
                    complete_qty = parse_decimal(row.get("Complete Qty")) or Decimal(0)

                    if is_complete:
                        erp_status = WorksOrderOperation.STATUS_COMPLETED
                    elif is_released and complete_qty > 0:
                        erp_status = WorksOrderOperation.STATUS_WIP
                    elif is_released:
                        erp_status = WorksOrderOperation.STATUS_RELEASED
                    elif is_firm:
                        erp_status = WorksOrderOperation.STATUS_FIRM_PLANNED
                    else:
                        erp_status = WorksOrderOperation.STATUS_NEW_ORDER

                    op = WorksOrderOperation.query.filter_by(
                        so_number=so_number,
                        line_number=line_number,
                        work_centre_name=wc_name,
                    ).first()

                    if op:
                        # Update ERP fields; only force status when ERP says completed
                        op.qty = op_qty
                        op.due_date = op_due_date
                        op.department_id = dept_id
                        op.imported_at = now
                        if erp_status == WorksOrderOperation.STATUS_COMPLETED:
                            op.status = WorksOrderOperation.STATUS_COMPLETED
                            if op.completed_date is None:
                                op.completed_date = date.today()
                        rows_updated += 1
                    else:
                        op = WorksOrderOperation(
                            sales_order_line_id=sol.id,
                            department_id=dept_id,
                            so_number=so_number,
                            line_number=line_number,
                            work_centre_name=wc_name,
                            qty=op_qty,
                            due_date=op_due_date,
                            status=erp_status,
                            imported_at=now,
                        )
                        db.session.add(op)
                        rows_inserted += 1


            # â”€â”€ Close absent non-terminal operations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ #
            terminal = {WorksOrderOperation.STATUS_COMPLETED, WorksOrderOperation.STATUS_CLOSED}
            today = date.today()
            closed_sol_keys: set[tuple] = set()

            open_ops = WorksOrderOperation.query.filter(
                WorksOrderOperation.status.notin_(list(terminal))
            ).all()
            for op in open_ops:
                key = (op.so_number, op.line_number, op.work_centre_name)
                if key not in seen_op_keys:
                    op.status = WorksOrderOperation.STATUS_CLOSED
                    if op.completed_date is None:
                        op.completed_date = today
                    rows_closed += 1
                    closed_sol_keys.add((op.so_number, op.line_number))

            batch.rows_inserted = rows_inserted
            batch.rows_updated = rows_updated
            batch.rows_closed = rows_closed
            batch.status = ImportBatch.STATUS_SUCCESS
            db.session.commit()

        except Exception as exc:
            db.session.rollback()
            batch.status = ImportBatch.STATUS_FAILED
            batch.error_message = str(exc)
            try:
                db.session.add(batch)
                db.session.commit()
            except Exception:
                db.session.rollback()
            raise

        return batch


