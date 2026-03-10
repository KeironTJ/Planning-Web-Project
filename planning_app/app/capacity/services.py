"""
Capacity planning service layer.

Computes load vs available hours per department per week, using:
  - CapacityBucket  : available hours from LabourPlan_HIDE.csv import
  - WorksOrderOperation × SalesOrderLine × SmvMatrix : demand (load) hours
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, and_

from app.extensions import db
from app.orders.models import Department, WorksOrderOperation, SmvMatrix, SalesOrderLine
from .models import CapacityBucket


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _week_label(d: date) -> str:
    """Return ISO week label e.g. '2026-W12'."""
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _week_start(d: date) -> date:
    """Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def _period_weeks(from_date: date, num_weeks: int) -> list[tuple[date, date, str]]:
    """Return list of (week_start, week_end, label) for num_weeks starting from_date."""
    start = _week_start(from_date)
    weeks = []
    for i in range(num_weeks):
        ws = start + timedelta(weeks=i)
        we = ws + timedelta(days=6)
        weeks.append((ws, we, _week_label(ws)))
    return weeks


# ---------------------------------------------------------------------------
# Available hours
# ---------------------------------------------------------------------------

def get_available_by_week_dept(
    from_date: date,
    to_date: date,
) -> dict[tuple[str, int], float]:
    """Return dict keyed by (week_label, department_id) → total available hours."""
    rows = (
        db.session.query(
            CapacityBucket.week,
            CapacityBucket.department_id,
            func.sum(CapacityBucket.available_hours).label("total_hours"),
        )
        .filter(
            CapacityBucket.date >= from_date,
            CapacityBucket.date <= to_date,
            CapacityBucket.is_workday == True,  # noqa: E712
            CapacityBucket.available_hours.isnot(None),
        )
        .group_by(CapacityBucket.week, CapacityBucket.department_id)
        .all()
    )
    return {(row.week, row.department_id): float(row.total_hours or 0) for row in rows}


# ---------------------------------------------------------------------------
# Load hours (demand)
# ---------------------------------------------------------------------------

def get_load_by_week_dept(
    from_date: date,
    to_date: date,
) -> dict[tuple[str, int], float]:
    """
    Return dict keyed by (week_label, department_id) → total load hours.

    Load = SUM(qty × smv_minutes / 60) for operations with a planned_date
    in the period that have a matching SMV entry.
    """
    rows = (
        db.session.query(
            CapacityBucket.week,            # reuse bucket week labels for consistency
            WorksOrderOperation.department_id,
            func.sum(
                WorksOrderOperation.qty * SmvMatrix.smv_minutes / 60
            ).label("load_hours"),
        )
        .join(
            SalesOrderLine,
            WorksOrderOperation.sales_order_line_id == SalesOrderLine.id,
        )
        .join(
            SmvMatrix,
            and_(
                SmvMatrix.component_id == SalesOrderLine.product_code,
                SmvMatrix.department_id == WorksOrderOperation.department_id,
            ),
        )
        .join(
            CapacityBucket,
            and_(
                CapacityBucket.department_id == WorksOrderOperation.department_id,
                CapacityBucket.date == WorksOrderOperation.planned_date,
            ),
        )
        .filter(
            WorksOrderOperation.planned_date >= from_date,
            WorksOrderOperation.planned_date <= to_date,
            WorksOrderOperation.status.notin_([
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ]),
        )
        .group_by(CapacityBucket.week, WorksOrderOperation.department_id)
        .all()
    )
    return {(row.week, row.department_id): float(row.load_hours or 0) for row in rows}


# ---------------------------------------------------------------------------
# Main dashboard data
# ---------------------------------------------------------------------------

def get_capacity_dashboard(
    from_date: date,
    num_weeks: int = 13,
    dept_id: Optional[int] = None,
) -> dict:
    """
    Build all data needed for the capacity dashboard.

    Returns:
        weeks         — list of ISO week labels
        departments   — list of {dept, rows:[{week, avail, load, util}]}
        summary       — list of {dept, avail_total, load_total, util_pct, headroom}
        has_smv       — bool: SMV data is present
        has_buckets   — bool: LabourPlan data is present
        from_date/to_date
    """
    weeks = _period_weeks(from_date, num_weeks)
    from_dt = weeks[0][0]
    to_dt   = weeks[-1][1]

    dept_q = Department.query.filter_by(is_active=True).order_by(Department.name)
    if dept_id:
        dept_q = dept_q.filter(Department.id == dept_id)
    departments = dept_q.all()

    avail_map = get_available_by_week_dept(from_dt, to_dt)
    load_map  = get_load_by_week_dept(from_dt, to_dt)

    has_buckets = bool(avail_map)
    has_smv     = bool(load_map)

    dept_data = []
    summary   = []

    for dept in departments:
        rows        = []
        avail_total = 0.0
        load_total  = 0.0

        for ws, we, label in weeks:
            avail = avail_map.get((label, dept.id), 0.0)
            load  = load_map.get((label, dept.id), 0.0)
            util  = (load / avail * 100) if avail > 0 else 0.0
            rows.append({
                "week":       label,
                "week_start": ws.isoformat(),
                "avail":      round(avail, 1),
                "load":       round(load, 1),
                "util":       round(util, 1),
            })
            avail_total += avail
            load_total  += load

        dept_data.append({"dept": dept, "rows": rows})
        util_pct = (load_total / avail_total * 100) if avail_total > 0 else 0.0
        summary.append({
            "dept":        dept,
            "avail_total": round(avail_total, 1),
            "load_total":  round(load_total, 1),
            "util_pct":    round(util_pct, 1),
            "headroom":    round(avail_total - load_total, 1),
        })

    return {
        "weeks":       [w[2] for w in weeks],
        "weeks_raw":   weeks,
        "departments": dept_data,
        "summary":     summary,
        "has_smv":     has_smv,
        "has_buckets": has_buckets,
        "from_date":   from_dt,
        "to_date":     to_dt,
    }


# ---------------------------------------------------------------------------
# Department calendar (day-by-day LabourPlan view)
# ---------------------------------------------------------------------------

def get_dept_calendar(
    dept_id: int,
    from_date: date,
    num_weeks: int = 8,
) -> dict:
    """
    Return CapacityBucket rows for a department for calendar display.
    """
    dept = Department.query.get_or_404(dept_id)
    ws       = _week_start(from_date)
    to_date  = ws + timedelta(weeks=num_weeks) - timedelta(days=1)

    buckets = (
        CapacityBucket.query
        .filter(
            CapacityBucket.department_id == dept_id,
            CapacityBucket.date >= ws,
            CapacityBucket.date <= to_date,
        )
        .order_by(CapacityBucket.date)
        .all()
    )
    bucket_by_date = {b.date: b for b in buckets}

    weeks_out = []
    for i in range(num_weeks):
        week_start_date = ws + timedelta(weeks=i)
        label = _week_label(week_start_date)
        days = []
        for d in range(7):
            day_date = week_start_date + timedelta(days=d)
            days.append({"date": day_date, "bucket": bucket_by_date.get(day_date)})
        weeks_out.append({"label": label, "days": days})

    return {
        "dept":      dept,
        "weeks":     weeks_out,
        "from_date": ws,
        "to_date":   to_date,
    }


# ---------------------------------------------------------------------------
# Bucket override
# ---------------------------------------------------------------------------

def override_bucket(bucket_id: int, new_hours: float) -> CapacityBucket:
    """Manually set available_hours on a bucket and mark it as overridden."""
    from app.core.exceptions import NotFoundError
    bucket = CapacityBucket.query.get(bucket_id)
    if bucket is None:
        raise NotFoundError(f"CapacityBucket {bucket_id} not found")
    bucket.available_hours = new_hours
    bucket.manually_overridden = True
    db.session.commit()
    return bucket
