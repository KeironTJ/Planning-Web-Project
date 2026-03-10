"""
Capacity CSV importers.

Covers:
- LabourPlanImporter — LabourPlan_HIDE.csv (wide→long unpivot, full replace)

The LabourPlan CSV is wide format: one row per calendar day, one column per
department. The importer unpivots it to one CapacityBucket row per dept per day.

Columns to skip (not departments):
  Date, Week, Day, WorkDay?, Day Complete, Hours, Total FTE
"""

from datetime import datetime, timezone

from app.extensions import db
from app.core.csv_utils import read_csv_rows, excel_serial_to_date, parse_decimal, parse_bool_truefalse, parse_bool_yn
from app.orders.models import ImportBatch, Department
from .models import CapacityBucket

# Non-department columns in the LabourPlan CSV
_LABOUR_NON_DEPT_COLS = {"Date", "Week", "Day", "WorkDay?", "Day Complete", "Hours", "Total FTE"}


class LabourPlanImporter:
    """
    Import LabourPlan_HIDE.csv into capacity_buckets.

    Strategy: full replace — truncate all non-manually-overridden buckets,
    then reload from CSV. Manually overridden buckets are preserved.

    The CSV has one row per day. Each department column holds the available
    hours for that department on that day (blank = 0 or non-working day).
    """

    @staticmethod
    def import_file(source, uploaded_by_id=None, filename=None) -> ImportBatch:
        batch = ImportBatch(
            import_type=ImportBatch.TYPE_LABOUR_PLAN,
            filename=filename or "LabourPlan_HIDE.csv",
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

            # Identify department columns from the header
            header_cols = list(all_rows[0].keys())
            dept_cols = [c for c in header_cols if c not in _LABOUR_NON_DEPT_COLS]

            # Pre-load department lookup by name (case-insensitive)
            dept_lookup: dict[str, Department] = {
                d.name.lower(): d for d in Department.query.all()
            }

            # Full replace: delete all non-manually-overridden buckets
            CapacityBucket.query.filter_by(manually_overridden=False).delete()
            db.session.flush()

            for row in all_rows:
                date_val = excel_serial_to_date(row.get("Date"))
                if date_val is None:
                    continue

                week = row.get("Week") or None
                is_workday = parse_bool_truefalse(row.get("WorkDay?"), default=False)
                day_complete = parse_bool_yn(row.get("Day Complete"), default=False)

                for col in dept_cols:
                    dept = dept_lookup.get(col.lower())
                    if dept is None:
                        continue  # unknown department column — skip

                    raw_val = row.get(col, "").strip()
                    available_hours = parse_decimal(raw_val) if raw_val else None

                    bucket = CapacityBucket(
                        department_id=dept.id,
                        date=date_val,
                        week=week,
                        is_workday=is_workday,
                        day_complete=day_complete,
                        available_hours=available_hours,
                        manually_overridden=False,
                        imported_at=now,
                    )
                    db.session.add(bucket)
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
