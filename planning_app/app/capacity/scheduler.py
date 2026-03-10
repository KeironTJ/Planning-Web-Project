"""
Backward scheduling service.

Algorithm:
1. Load the routing template (default or specified).
2. Build a map of {dept_id: (sequence_order, lead_time_days)}.
3. For each open SO line that has a due_date:
   a. Collect its open WorksOrderOperations.
   b. Map each operation's department to a sequence_order.
   c. Group operations by sequence_order (parallel groups).
   d. Walk backwards from due_date, stage by stage (latest first).
      - Each stage's planned_date = stage_end_date − max(LT in stage) working days.
      - The next stage's end = this stage's planned_date.
   e. Write planned_date to each operation (respecting skip-manual flag).

Working day calendar:
  Sourced from CapacityBucket.is_workday (populated by LabourPlan import).
  Falls back to Mon–Thu (weekday < 4) when no bucket data exists for a date.
"""

from __future__ import annotations

import bisect
from datetime import date, timedelta
from itertools import groupby
from typing import Optional

from app.extensions import db
from app.orders.models import SalesOrderLine, WorksOrderOperation
from .models import CapacityBucket, RoutingTemplate


# ---------------------------------------------------------------------------
# Working-day helpers
# ---------------------------------------------------------------------------

def _load_working_days(from_date: date, to_date: date) -> list[date]:
    """
    Return a sorted list of distinct working days in [from_date, to_date]
    drawn from CapacityBucket rows where is_workday=True.
    """
    rows = (
        db.session.query(CapacityBucket.date)
        .filter(
            CapacityBucket.is_workday == True,  # noqa: E712
            CapacityBucket.date >= from_date,
            CapacityBucket.date <= to_date,
        )
        .distinct()
        .order_by(CapacityBucket.date)
        .all()
    )
    return [r.date for r in rows]


def _is_working_day_fallback(d: date) -> bool:
    """4-day week fallback: Mon–Thu (weekday 0–3) when no bucket data."""
    return d.weekday() < 4


def subtract_working_days(
    from_date: date,
    days: int,
    working_days: list[date],
) -> date:
    """
    Return the date that is `days` working days before `from_date`.

    Uses the sorted `working_days` list for accuracy.  If the list doesn't
    extend far enough backwards, falls back to the 4-day-week rule.
    """
    if days <= 0:
        return from_date

    # Find the position at or before from_date in the sorted list
    pos = bisect.bisect_right(working_days, from_date) - 1

    if pos >= days:
        # Enough working days in the list
        return working_days[pos - days]

    # Not enough data — walk backwards from the earliest known working day
    # using the 4-day-week fallback
    remaining = days - (pos + 1)  # days we still need to count back
    cursor = working_days[0] if working_days else from_date
    while remaining > 0:
        cursor -= timedelta(days=1)
        if _is_working_day_fallback(cursor):
            remaining -= 1
    return cursor


# ---------------------------------------------------------------------------
# Main scheduler
# ---------------------------------------------------------------------------

def schedule_orders(
    overwrite_manual: bool = False,
    template_id: Optional[int] = None,
) -> dict:
    """
    Run backward scheduling for all open SO lines.

    Args:
        overwrite_manual: If False, skip operations that already have a
                          manually-set planned_date.
        template_id: ID of the RoutingTemplate to use.  None → default template.

    Returns:
        dict with keys: scheduled, skipped, no_due_date, no_dept, template_name
    """
    # ------------------------------------------------------------------ #
    # 1. Resolve routing template
    # ------------------------------------------------------------------ #
    if template_id:
        template = RoutingTemplate.query.get(template_id)
        if template is None:
            raise ValueError(f"Routing template {template_id} not found.")
    else:
        template = (
            RoutingTemplate.query
            .filter_by(is_default=True, is_active=True)
            .first()
        )
    if template is None:
        raise ValueError(
            "No default routing template found. "
            "Please create and mark a routing template as default."
        )

    # ------------------------------------------------------------------ #
    # 2. Build department → (sequence_order, lead_time_days) maps
    # ------------------------------------------------------------------ #
    dept_seq_map: dict[int, int] = {}   # dept_id → sequence_order
    dept_lt_map: dict[int, int] = {}    # dept_id → lead_time_days

    for stage in template.stages:
        for entry in stage.entries:
            dept_seq_map[entry.department_id] = stage.sequence_order
            dept_lt_map[entry.department_id] = entry.effective_lead_time

    # ------------------------------------------------------------------ #
    # 3. Load working-day calendar (±1 year around today)
    # ------------------------------------------------------------------ #
    today = date.today()
    working_days = _load_working_days(
        today - timedelta(days=365),
        today + timedelta(days=730),
    )

    # ------------------------------------------------------------------ #
    # 4. Collect SO lines that have open operations
    # ------------------------------------------------------------------ #
    open_sol_ids = [
        r[0]
        for r in db.session.query(WorksOrderOperation.sales_order_line_id)
        .filter(
            WorksOrderOperation.status.notin_([
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ])
        )
        .distinct()
        .all()
    ]

    scheduled = 0
    skipped = 0
    no_due_date = 0
    no_dept = 0

    # ------------------------------------------------------------------ #
    # 5. Schedule each SO line
    # ------------------------------------------------------------------ #
    for sol_id in open_sol_ids:
        sol = SalesOrderLine.query.get(sol_id)
        if sol is None or not sol.due_date:
            no_due_date += 1
            continue

        open_ops = [
            op for op in sol.operations
            if op.status not in (
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            )
        ]
        if not open_ops:
            continue

        # Map each operation to (sequence_order, lead_time)
        # Departments not in the routing template go to sequence 9999
        # (after everything else) using the department's own default LT.
        op_tuples: list[tuple[WorksOrderOperation, int, int]] = []
        for op in open_ops:
            if not op.department_id:
                no_dept += 1
                continue
            seq = dept_seq_map.get(op.department_id, 9999)
            lt = dept_lt_map.get(
                op.department_id,
                op.department.default_lead_time_days if op.department else 2,
            )
            op_tuples.append((op, seq, lt))

        if not op_tuples:
            continue

        # Sort descending by sequence (last stage first → closest to due_date)
        op_tuples.sort(key=lambda x: x[1], reverse=True)

        # Group by sequence_order
        stage_end = sol.due_date
        for seq, group_iter in groupby(op_tuples, key=lambda x: x[1]):
            group = list(group_iter)
            max_lt = max(lt for _, _, lt in group)

            planned = subtract_working_days(stage_end, max_lt, working_days)

            for op, _, _ in group:
                if not overwrite_manual and op.planned_date is not None:
                    skipped += 1
                    continue
                op.planned_date = planned
                scheduled += 1

            # This stage's planned_date becomes the end point for the next
            # (earlier) stage
            stage_end = planned

    db.session.commit()

    return {
        "scheduled":   scheduled,
        "skipped":     skipped,
        "no_due_date": no_due_date,
        "no_dept":     no_dept,
        "template_name": template.name,
    }
