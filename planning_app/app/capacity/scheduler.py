"""
Scheduling service — backward and forward modes.

Backward scheduling (default):
  Walks backwards from the ERP due_date through routing stages.
  Used when due_date is in the future relative to the floor_date.

Forward scheduling (overdue orders):
  Walks forward from floor_date through routing stages.
  Used when floor_date >= due_date (order is overdue or being replanned
  to a date beyond the original ERP due date).

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
    Falls back to 4-day-week rule if the calendar doesn't extend far enough.
    """
    if days <= 0:
        return from_date

    pos = bisect.bisect_right(working_days, from_date) - 1

    if pos >= days:
        return working_days[pos - days]

    remaining = days - (pos + 1)
    cursor = working_days[0] if working_days else from_date
    while remaining > 0:
        cursor -= timedelta(days=1)
        if _is_working_day_fallback(cursor):
            remaining -= 1
    return cursor


def add_working_days(
    from_date: date,
    days: int,
    working_days: list[date],
) -> date:
    """
    Return the date that is `days` working days after `from_date`.
    Falls back to 4-day-week rule if the calendar doesn't extend far enough.
    """
    if days <= 0:
        return from_date

    pos = bisect.bisect_right(working_days, from_date)

    if pos + days <= len(working_days):
        return working_days[pos + days - 1]

    remaining = days - (len(working_days) - pos)
    cursor = working_days[-1] if working_days else from_date
    while remaining > 0:
        cursor += timedelta(days=1)
        if _is_working_day_fallback(cursor):
            remaining -= 1
    return cursor


# ---------------------------------------------------------------------------
# Main scheduler
# ---------------------------------------------------------------------------

def schedule_orders(
    overwrite_manual: bool = False,
    template_id: Optional[int] = None,
    sol_ids: Optional[list] = None,
    floor_date: Optional[date] = None,
) -> dict:
    """
    Schedule open SO lines using the routing template.

    When floor_date is provided:
      - If the order's due_date is AFTER floor_date: backward schedule from
        due_date, clamping any result that falls before floor_date up to
        floor_date.
      - If the order's due_date is ON or BEFORE floor_date (overdue /
        replanning past the due date): forward schedule from floor_date,
        walking through stages in order (first stage → last stage).

    Args:
        overwrite_manual: If False, skip operations that already have a planned_date.
        template_id: ID of the RoutingTemplate to use. None → default template.
        sol_ids: If provided, only schedule these SalesOrderLine IDs.
        floor_date: Earliest date any operation may be planned on.

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
    dept_seq_map: dict[int, int] = {}
    dept_lt_map: dict[int, int] = {}

    for stage in template.stages:
        for entry in stage.entries:
            dept_seq_map[entry.department_id] = stage.sequence_order
            dept_lt_map[entry.department_id] = entry.effective_lead_time

    # ------------------------------------------------------------------ #
    # 3. Load working-day calendar (±1 year around today, +2 years ahead)
    # ------------------------------------------------------------------ #
    today = date.today()
    anchor = floor_date or today
    working_days = _load_working_days(
        min(today, anchor) - timedelta(days=30),
        max(today, anchor) + timedelta(days=730),
    )

    # ------------------------------------------------------------------ #
    # 4. Collect SO lines with open operations
    # ------------------------------------------------------------------ #
    q = (
        db.session.query(WorksOrderOperation.sales_order_line_id)
        .filter(
            WorksOrderOperation.status.notin_([
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ])
        )
        .distinct()
    )
    if sol_ids:
        q = q.filter(WorksOrderOperation.sales_order_line_id.in_(sol_ids))
    open_sol_ids = [r[0] for r in q.all()]

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
        # Departments not in the routing template go to sequence 9999.
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

        # ── Choose scheduling direction ──────────────────────────────── #
        # Forward when floor_date is at or beyond the due date (overdue /
        # replanning past ERP due date).  Backward otherwise.
        use_forward = floor_date is not None and sol.due_date <= floor_date

        if use_forward:
            # ── Forward scheduling from floor_date ───────────────────── #
            # Sort ascending: first stage (lowest seq) gets the earliest date.
            op_tuples.sort(key=lambda x: x[1])
            stage_start = floor_date

            for seq, group_iter in groupby(op_tuples, key=lambda x: x[1]):
                group = list(group_iter)
                max_lt = max(lt for _, _, lt in group)

                for op, _, _ in group:
                    if not overwrite_manual and op.planned_date is not None:
                        skipped += 1
                        continue
                    op.planned_date = stage_start
                    scheduled += 1

                # Advance start for the next stage
                stage_start = add_working_days(stage_start, max_lt, working_days)

        else:
            # ── Backward scheduling from due_date ────────────────────── #
            # Sort descending: last stage (highest seq) first.
            op_tuples.sort(key=lambda x: x[1], reverse=True)
            stage_end = sol.due_date

            for seq, group_iter in groupby(op_tuples, key=lambda x: x[1]):
                group = list(group_iter)
                max_lt = max(lt for _, _, lt in group)

                planned = subtract_working_days(stage_end, max_lt, working_days)

                effective_planned = planned
                if floor_date and effective_planned < floor_date:
                    effective_planned = floor_date

                for op, _, _ in group:
                    if not overwrite_manual and op.planned_date is not None:
                        skipped += 1
                        continue
                    op.planned_date = effective_planned
                    scheduled += 1

                # This stage's unmodified date becomes the next stage's end
                stage_end = planned

    db.session.commit()

    return {
        "scheduled":   scheduled,
        "skipped":     skipped,
        "no_due_date": no_due_date,
        "no_dept":     no_dept,
        "template_name": template.name,
    }
