"""
Orders CSV importers.

Covers:
- OobImporter       — OpenOrderBook_HIDE.csv (UPSERT, preserves planner fields)
- SmvImporter       — SMVTable_HIDE.csv (wide→long unpivot, UPSERT)
- ProductionFlowImporter — ProductionFlowLT_HIDE.csv (full replace)
"""

from datetime import datetime, timezone

from app.extensions import db
from app.core.csv_utils import (
    read_csv_rows, excel_serial_to_date, parse_decimal, parse_int, parse_bool_yn,
)
from .models import (
    Department, SalesOrderLine, WorksOrderOperation,
    SmvMatrix, ProductionFlow, ImportBatch,
)

# Columns in the OOB that belong to the parent SalesOrderLine
_SOL_ERP_FIELDS = {
    "customer_code", "customer_name", "customer_order_ref", "customer_product_ref",
    "order_type", "caravan_code", "caravan_description",
    "product_code", "product_description", "qty_ordered",
    "order_date", "due_date", "unit_price", "total_value",
}

# Columns in the SMV CSV that are NOT department SMV values
_SMV_NON_DEPT_COLS = {"COMPONENT ID", "TIMING CODE", "DESCRIPTION", "OPS", "Date Updated"}

# Columns in the ProductionFlow CSV that are NOT department lead-time values
_FLOW_NON_DEPT_COLS = {"UNIQUE FLOW", "Production Flow", "Ops", "Lead Time Q", "Firmed?"}


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
                    "caravan_code": first.get("CARAVANCODE") or None,
                    "caravan_description": first.get("CARAVANDESCRIPTION") or None,
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
                        # Update ERP fields only — never touch planner fields
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
                            status=WorksOrderOperation.STATUS_NOT_STARTED,
                            imported_at=now,
                        )
                        db.session.add(op)
                        rows_inserted += 1

            # --- 3.5. Set / clear ops_missing flag on every SOL seen in this import ---
            # Build a set of (so_number, line_number) pairs that have at least one op in this import.
            sol_keys_with_ops: set[tuple] = {
                (so_num, line_num)
                for (so_num, line_num, _wc) in seen_op_keys
            }

            for (so_number, line_number_str), _rows in groups.items():
                line_number = parse_int(line_number_str)
                if not so_number or line_number is None:
                    continue
                sol = SalesOrderLine.query.filter_by(
                    so_number=so_number, line_number=line_number
                ).first()
                if sol is None:
                    continue
                has_ops = (so_number, line_number) in sol_keys_with_ops
                if has_ops:
                    sol.ops_missing = False
                else:
                    if not sol.ops_missing:
                        sol.ops_missing = True
                        sol.ops_missing_since = now

            # --- 4. Mark absent operations as closed ---
            # OOB is the authoritative source — if absent, the order has shipped/cancelled in ERP.
            # Close everything that isn't already in a terminal state.
            terminal_statuses = [
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ]
            open_ops = WorksOrderOperation.query.filter(
                WorksOrderOperation.status.notin_(terminal_statuses)
            ).all()

            for op in open_ops:
                key = (op.so_number, op.line_number, op.work_centre_name)
                if key not in seen_op_keys:
                    op.status = WorksOrderOperation.STATUS_CLOSED
                    rows_closed += 1

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
# SMV Importer
# ---------------------------------------------------------------------------

class SmvImporter:
    """
    Import SMVTable_HIDE.csv into smv_matrix.

    The CSV is wide format: one row per component, one column per department.
    We unpivot to long format: one row per component × department.

    Strategy: UPSERT on (component_id, department_id).
    Planner-set confidence is preserved on update.
    """

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = ImportBatch(
            import_type=ImportBatch.TYPE_SMV,
            filename=filename or "SMVTable_HIDE.csv",
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

            if not all_rows:
                batch.status = ImportBatch.STATUS_SUCCESS
                db.session.commit()
                return batch

            # Identify department columns from the header
            header_cols = list(all_rows[0].keys())
            dept_cols = [c for c in header_cols if c not in _SMV_NON_DEPT_COLS]

            # Pre-load department lookup by name (case-insensitive)
            dept_lookup: dict[str, Department] = {
                d.name.lower(): d for d in Department.query.all()
            }

            for row in all_rows:
                component_id = row.get("COMPONENT ID", "").strip()
                if not component_id:
                    continue

                timing_code = row.get("TIMING CODE") or None
                description = row.get("DESCRIPTION") or None
                ops = parse_int(row.get("OPS"))
                date_updated_str = row.get("Date Updated") or None
                date_updated = None
                if date_updated_str:
                    date_updated = excel_serial_to_date(date_updated_str)

                for col in dept_cols:
                    raw_val = row.get(col, "").strip()
                    smv_val = parse_decimal(raw_val)

                    # Skip if no department match in DB
                    dept = dept_lookup.get(col.lower())
                    if dept is None:
                        continue

                    existing = SmvMatrix.query.filter_by(
                        component_id=component_id,
                        department_id=dept.id,
                    ).first()

                    if existing:
                        existing.timing_code = timing_code
                        existing.description = description
                        existing.smv_minutes = smv_val
                        existing.ops = ops
                        existing.date_updated = date_updated
                        # confidence is NOT overwritten — planner controls it
                        existing.last_modified_at = now
                        existing.last_modified_by_id = uploaded_by_id
                        rows_updated += 1
                    else:
                        entry = SmvMatrix(
                            component_id=component_id,
                            timing_code=timing_code,
                            description=description,
                            department_id=dept.id,
                            smv_minutes=smv_val,
                            ops=ops,
                            date_updated=date_updated,
                            confidence=SmvMatrix.CONFIDENCE_ESTIMATED,
                            last_modified_at=now,
                            last_modified_by_id=uploaded_by_id,
                        )
                        db.session.add(entry)
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
# Production Flow Importer
# ---------------------------------------------------------------------------

class ProductionFlowImporter:
    """
    Import ProductionFlowLT_HIDE.csv into production_flows.

    Strategy: full replace (truncate + reload).
    dept_lead_times stored as JSON {dept_name: days}.
    """

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = ImportBatch(
            import_type=ImportBatch.TYPE_PRODUCTION_FLOW,
            filename=filename or "ProductionFlowLT_HIDE.csv",
            uploaded_by_id=uploaded_by_id,
            status=ImportBatch.STATUS_PENDING,
        )
        db.session.add(batch)
        db.session.flush()

        now = datetime.now(timezone.utc)
        rows_inserted = 0

        try:
            all_rows = list(read_csv_rows(source))
            batch.row_count = len(all_rows)

            if not all_rows:
                batch.status = ImportBatch.STATUS_SUCCESS
                db.session.commit()
                return batch

            # Identify department lead-time columns
            header_cols = list(all_rows[0].keys())
            dept_cols = [c for c in header_cols if c not in _FLOW_NON_DEPT_COLS]

            # Full replace
            ProductionFlow.query.delete()
            db.session.flush()

            for row in all_rows:
                unique_flow = row.get("UNIQUE FLOW", "").strip()
                if not unique_flow:
                    continue

                dept_lead_times = {}
                for col in dept_cols:
                    days = parse_int(row.get(col))
                    if days is not None and days > 0:
                        dept_lead_times[col] = days

                flow = ProductionFlow(
                    unique_flow=unique_flow,
                    flow_description=row.get("Production Flow") or None,
                    ops=parse_int(row.get("Ops")),
                    total_lead_time_days=parse_int(row.get("Lead Time Q")),
                    firmed=parse_bool_yn(row.get("Firmed?"), default=False),
                    dept_lead_times=dept_lead_times if dept_lead_times else None,
                    imported_at=now,
                )
                db.session.add(flow)
                rows_inserted += 1

            batch.rows_inserted = rows_inserted
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
