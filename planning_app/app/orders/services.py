"""
Orders service layer.

Contains all query and business logic for the WIP tracker and related views.
Routes should call these functions rather than querying models directly.
"""

import math
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, case, and_

from app.extensions import db
from app.core.exceptions import NotFoundError, ValidationError
from .models import Department, SalesOrderLine, WorksOrderOperation


# Operation status priority (lowest = least complete = worst)
_STATUS_PRIORITY = {
    WorksOrderOperation.STATUS_NOT_STARTED: 0,
    WorksOrderOperation.STATUS_STARTED:     1,
    WorksOrderOperation.STATUS_WIP:         2,
    WorksOrderOperation.STATUS_COMPLETED:   3,
    WorksOrderOperation.STATUS_CLOSED:      4,
}

_NEXT_STATUS = {
    WorksOrderOperation.STATUS_NOT_STARTED: WorksOrderOperation.STATUS_STARTED,
    WorksOrderOperation.STATUS_STARTED:     WorksOrderOperation.STATUS_WIP,
    WorksOrderOperation.STATUS_WIP:         WorksOrderOperation.STATUS_COMPLETED,
    WorksOrderOperation.STATUS_COMPLETED:   WorksOrderOperation.STATUS_COMPLETED,
}

_PREV_STATUS = {
    WorksOrderOperation.STATUS_STARTED:   WorksOrderOperation.STATUS_NOT_STARTED,
    WorksOrderOperation.STATUS_WIP:       WorksOrderOperation.STATUS_STARTED,
    WorksOrderOperation.STATUS_COMPLETED: WorksOrderOperation.STATUS_WIP,
}


class SimplePagination:
    """Minimal pagination proxy for in-Python grouped queries."""

    def __init__(self, items: list, total: int, page: int, per_page: int):
        self.items    = items
        self.total    = total
        self.page     = page
        self.per_page = per_page

    @property
    def pages(self) -> int:
        return math.ceil(self.total / self.per_page) if self.per_page else 0

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    def iter_pages(self, left_edge=1, right_edge=1, left_current=2, right_current=2):
        last = 0
        for num in range(1, self.pages + 1):
            if (
                num <= left_edge
                or (self.page - left_current) <= num <= (self.page + right_current)
                or num > self.pages - right_edge
            ):
                if last + 1 != num:
                    yield None
                yield num
                last = num


# ---------------------------------------------------------------------------
# WIP Tracker queries
# ---------------------------------------------------------------------------

def get_wip_page(
    *,
    page: int = 1,
    per_page: int = 50,
    dept_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    overdue_only: bool = False,
    order_by: str = "due_date",
):
    """
    Return paginated SalesOrderLines for the WIP tracker.

    Each line has its operations eager-loaded so the template can render the
    per-department status grid without N+1 queries.
    """
    from sqlalchemy.orm import joinedload

    q = SalesOrderLine.query.options(
        joinedload(SalesOrderLine.operations).joinedload(WorksOrderOperation.department)
    )

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                SalesOrderLine.so_number.ilike(term),
                SalesOrderLine.customer_name.ilike(term),
                SalesOrderLine.product_code.ilike(term),
                SalesOrderLine.product_description.ilike(term),
                SalesOrderLine.customer_order_ref.ilike(term),
                SalesOrderLine.customer_product_ref.ilike(term),
            )
        )

    if status_filter:
        # Filter to lines that have at least one operation with the given status
        q = q.filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.status == status_filter
            )
        )

    if dept_filter:
        q = q.filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.work_centre_name == dept_filter
            )
        )

    if overdue_only:
        today = date.today()
        q = q.filter(
            SalesOrderLine.due_date < today,
            SalesOrderLine.operations.any(
                and_(
                    WorksOrderOperation.status.notin_([
                        WorksOrderOperation.STATUS_COMPLETED,
                        WorksOrderOperation.STATUS_CLOSED,
                    ]),
                    WorksOrderOperation.due_date < today,
                )
            ),
        )

    # Exclude fully-closed lines by default
    q = q.filter(
        SalesOrderLine.operations.any(
            WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED
        )
    )

    if order_by == "due_date":
        q = q.order_by(SalesOrderLine.due_date.asc().nullslast(), SalesOrderLine.so_number)
    elif order_by == "so_number":
        q = q.order_by(SalesOrderLine.so_number, SalesOrderLine.line_number)
    elif order_by == "customer":
        q = q.order_by(SalesOrderLine.customer_name, SalesOrderLine.due_date)
    else:
        q = q.order_by(SalesOrderLine.due_date.asc().nullslast(), SalesOrderLine.so_number)

    return q.paginate(page=page, per_page=per_page, error_out=False)


def get_wip_grouped(
    *,
    page: int = 1,
    per_page: int = 25,
    dept_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    cust_prod_ref: Optional[str] = None,
    overdue_only: bool = False,
    order_by: str = "due_date",
    due_date_from: Optional[date] = None,
    due_date_to: Optional[date] = None,
    planned_date_from: Optional[date] = None,
    planned_date_to: Optional[date] = None,
) -> tuple["SimplePagination", list]:
    """
    Return WIP data grouped by SO number.

    Each item in the returned list is a dict:
        so_number      str
        customer_name  str
        due_date       date | None   — earliest due date across lines
        total_qty      Decimal       — sum of qty_ordered across lines
        line_count     int
        agg_status     str           — worst line-level status across lines
        dept_status    dict[str, str] — dept_name → min op status (worst first)
        lines          list[SalesOrderLine]
    """
    from sqlalchemy.orm import joinedload

    today = date.today()

    q = SalesOrderLine.query.options(
        joinedload(SalesOrderLine.operations).joinedload(WorksOrderOperation.department)
    )

    # Exclude fully-closed lines
    q = q.filter(
        SalesOrderLine.operations.any(
            WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED
        )
    )

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(db.or_(
            SalesOrderLine.so_number.ilike(term),
            SalesOrderLine.customer_name.ilike(term),
            SalesOrderLine.product_code.ilike(term),
            SalesOrderLine.product_description.ilike(term),
            SalesOrderLine.customer_order_ref.ilike(term),
            SalesOrderLine.customer_product_ref.ilike(term),
        ))

    if cust_prod_ref:
        q = q.filter(SalesOrderLine.customer_product_ref.ilike(f"%{cust_prod_ref.strip()}%"))

    if status_filter:
        q = q.filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.status == status_filter
            )
        )

    if dept_filter:
        q = q.filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.work_centre_name == dept_filter
            )
        )

    if overdue_only:
        q = q.filter(
            SalesOrderLine.due_date < today,
            SalesOrderLine.operations.any(
                and_(
                    WorksOrderOperation.status.notin_([
                        WorksOrderOperation.STATUS_COMPLETED,
                        WorksOrderOperation.STATUS_CLOSED,
                    ]),
                    WorksOrderOperation.due_date < today,
                )
            ),
        )

    if due_date_from:
        q = q.filter(SalesOrderLine.due_date >= due_date_from)
    if due_date_to:
        q = q.filter(SalesOrderLine.due_date <= due_date_to)
    if planned_date_from:
        q = q.filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.planned_date >= planned_date_from
            )
        )
    if planned_date_to:
        q = q.filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.planned_date <= planned_date_to
            )
        )

    # Order lines so grouping is stable
    # plan_date sort is applied after grouping; use due_date as DB default for it
    if order_by == "so_number":
        q = q.order_by(SalesOrderLine.so_number, SalesOrderLine.line_number)
    elif order_by == "customer":
        q = q.order_by(
            SalesOrderLine.customer_name,
            SalesOrderLine.due_date.asc().nullslast(),
            SalesOrderLine.so_number,
            SalesOrderLine.line_number,
        )
    else:  # due_date (default)
        q = q.order_by(
            SalesOrderLine.due_date.asc().nullslast(),
            SalesOrderLine.so_number,
            SalesOrderLine.line_number,
        )

    all_lines = q.all()

    # Group by so_number maintaining query order
    seen: dict[str, dict] = {}
    order_list: list[dict] = []
    for sol in all_lines:
        if sol.so_number not in seen:
            entry = {
                "so_number":     sol.so_number,
                "customer_name": sol.customer_name or "",
                "lines":         [],
            }
            seen[sol.so_number] = entry
            order_list.append(entry)
        seen[sol.so_number]["lines"].append(sol)

    # Compute aggregates for each order group
    _line_status_priority = {
        SalesOrderLine.LINE_STATUS_NEW:          0,
        SalesOrderLine.LINE_STATUS_FIRM_PLANNED: 1,
        SalesOrderLine.LINE_STATUS_WIP:          2,
        SalesOrderLine.LINE_STATUS_COMPLETE:     3,
    }

    for entry in order_list:
        lines = entry["lines"]

        # Earliest due date
        due_dates = [s.due_date for s in lines if s.due_date]
        entry["due_date"] = min(due_dates) if due_dates else None

        # Total qty
        entry["total_qty"] = sum(
            (s.qty_ordered or 0) for s in lines
        )
        entry["line_count"] = len(lines)

        # Aggregate line status (worst = lowest priority)
        line_statuses = [s.aggregate_status for s in lines]
        entry["agg_status"] = min(
            line_statuses,
            key=lambda s: _line_status_priority.get(s, 99),
        )

        # Dept status: dept_name → min op status across all lines for this SO
        dept_ops: dict[str, list] = {}
        for sol in lines:
            for op in sol.operations:
                if op.status != WorksOrderOperation.STATUS_CLOSED:
                    dept_ops.setdefault(op.work_centre_name, []).append(op)

        entry["dept_status"] = {
            dept_name: min(ops, key=lambda o: _STATUS_PRIORITY.get(o.status, 99)).status
            for dept_name, ops in dept_ops.items()
        }

        # Dept planned: dept_name → earliest planned_date across all lines for this SO
        entry["dept_planned"] = {
            dept_name: min(
                (op.planned_date for op in ops if op.planned_date),
                default=None,
            )
            for dept_name, ops in dept_ops.items()
        }

        # Latest planned date across all open ops — represents expected completion
        all_planned = [
            op.planned_date
            for sol in lines
            for op in sol.operations
            if op.planned_date and op.status != WorksOrderOperation.STATUS_CLOSED
        ]
        entry["plan_date"]  = max(all_planned) if all_planned else None
        entry["plan_start"] = min(all_planned) if all_planned else None

        # Unique CPRs across all lines (preserving order, excluding nulls)
        entry["cpr_list"] = list(dict.fromkeys(
            sol.customer_product_ref for sol in lines
            if sol.customer_product_ref
        ))

    # Apply plan_date sort after aggregation (can't do this at DB level)
    if order_by == "plan_date":
        order_list.sort(key=lambda e: e["plan_date"] or date.max)

    # Manual pagination
    total = len(order_list)
    start = (page - 1) * per_page
    page_items = order_list[start: start + per_page]

    return SimplePagination(page_items, total, page, per_page), page_items


def advance_so_dept_status(so_number: str, work_centre_name: str) -> dict:
    """
    Advance every open operation for a given SO + work centre to its next status.
    Returns the new aggregate status for that SO + dept cell.
    """
    ops = (
        WorksOrderOperation.query
        .filter_by(so_number=so_number, work_centre_name=work_centre_name)
        .filter(WorksOrderOperation.status.notin_([
            WorksOrderOperation.STATUS_COMPLETED,
            WorksOrderOperation.STATUS_CLOSED,
        ]))
        .all()
    )

    for op in ops:
        next_s = _NEXT_STATUS.get(op.status, op.status)
        op.status = next_s
        if next_s == WorksOrderOperation.STATUS_COMPLETED and op.completed_date is None:
            op.completed_date = date.today()

    db.session.commit()

    # Recompute aggregate for the cell (min status across all non-closed ops)
    remaining = (
        WorksOrderOperation.query
        .filter_by(so_number=so_number, work_centre_name=work_centre_name)
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .all()
    )
    if not remaining:
        agg = WorksOrderOperation.STATUS_COMPLETED
    else:
        agg = min(remaining, key=lambda o: _STATUS_PRIORITY.get(o.status, 99)).status

    label, colour = WorksOrderOperation.STATUS_META.get(agg, ("Unknown", "secondary"))
    return {"status": agg, "label": label, "colour": colour}


def reverse_so_dept_status(so_number: str, work_centre_name: str) -> dict:
    """
    Step every open operation for a given SO + work centre back one status.
    not_started is the floor — operations there are left unchanged.
    Returns the new aggregate status for that SO + dept cell.
    """
    ops = (
        WorksOrderOperation.query
        .filter_by(so_number=so_number, work_centre_name=work_centre_name)
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .all()
    )

    for op in ops:
        prev_s = _PREV_STATUS.get(op.status)
        if prev_s is None:
            continue  # already at not_started, leave it
        op.status = prev_s
        if prev_s != WorksOrderOperation.STATUS_COMPLETED:
            op.completed_date = None  # clear completed_date if stepping back

    db.session.commit()

    # Recompute aggregate
    remaining = (
        WorksOrderOperation.query
        .filter_by(so_number=so_number, work_centre_name=work_centre_name)
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .all()
    )
    if not remaining:
        agg = WorksOrderOperation.STATUS_COMPLETED
    else:
        agg = min(remaining, key=lambda o: _STATUS_PRIORITY.get(o.status, 99)).status

    label, colour = WorksOrderOperation.STATUS_META.get(agg, ("Unknown", "secondary"))
    return {"status": agg, "label": label, "colour": colour}


def get_dept_operations(
    dept_id: int,
    *,
    page: int = 1,
    per_page: int = 50,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
):
    """
    Return paginated WorksOrderOperations for a single department.
    Used by the department-level order list view.
    """
    from sqlalchemy.orm import joinedload

    dept = Department.query.get(dept_id)
    if dept is None:
        raise NotFoundError(f"Department {dept_id} not found")

    q = (
        WorksOrderOperation.query
        .filter_by(department_id=dept_id)
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .options(joinedload(WorksOrderOperation.sales_order_line))
        .order_by(WorksOrderOperation.due_date.asc().nullslast(), WorksOrderOperation.so_number)
    )

    if status_filter:
        q = q.filter(WorksOrderOperation.status == status_filter)

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                WorksOrderOperation.so_number.ilike(term),
                WorksOrderOperation.work_centre_name.ilike(term),
            )
        )

    return dept, q.paginate(page=page, per_page=per_page, error_out=False)


def get_firming_queue(page: int = 1, per_page: int = 25, search: Optional[str] = None, cust_prod_ref: Optional[str] = None):
    """
    Return orders not yet in production (New Order or Firm Planned), grouped by SO.

    Excludes any SO that already has a started / wip / completed operation.

    Each item is a dict:
        so_number           str
        customer_name       str
        due_date            date | None
        days_delta          int | None
        plan_start          date | None
        plan_end            date | None
        total_qty           Decimal
        total_value         Decimal
        line_count          int
        agg_status          str
        not_started_op_ids  list[int]   IDs of not_started ops (for Start Production)
        lines               list[SalesOrderLine]
    """
    from sqlalchemy.orm import joinedload

    today = date.today()

    production_statuses = [
        WorksOrderOperation.STATUS_STARTED,
        WorksOrderOperation.STATUS_WIP,
        WorksOrderOperation.STATUS_COMPLETED,
    ]

    q = (
        SalesOrderLine.query
        .filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.status == WorksOrderOperation.STATUS_NOT_STARTED
            )
        )
        .filter(
            ~SalesOrderLine.operations.any(
                WorksOrderOperation.status.in_(production_statuses)
            )
        )
        .options(joinedload(SalesOrderLine.operations).joinedload(WorksOrderOperation.department))
        .order_by(
            SalesOrderLine.due_date.asc().nullslast(),
            SalesOrderLine.so_number,
            SalesOrderLine.line_number,
        )
    )

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(db.or_(
            SalesOrderLine.so_number.ilike(term),
            SalesOrderLine.customer_name.ilike(term),
            SalesOrderLine.product_description.ilike(term),
            SalesOrderLine.customer_product_ref.ilike(term),
        ))

    if cust_prod_ref:
        q = q.filter(SalesOrderLine.customer_product_ref.ilike(f"%{cust_prod_ref.strip()}%"))

    all_lines = q.all()

    _line_status_priority = {
        SalesOrderLine.LINE_STATUS_NEW:          0,
        SalesOrderLine.LINE_STATUS_FIRM_PLANNED: 1,
        SalesOrderLine.LINE_STATUS_WIP:          2,
        SalesOrderLine.LINE_STATUS_COMPLETE:     3,
    }

    seen: dict[str, dict] = {}
    order_list: list[dict] = []

    for sol in all_lines:
        if sol.so_number not in seen:
            entry: dict = {
                "so_number":     sol.so_number,
                "customer_name": sol.customer_name or "",
                "lines":         [],
            }
            seen[sol.so_number] = entry
            order_list.append(entry)
        seen[sol.so_number]["lines"].append(sol)

    for entry in order_list:
        lines = entry["lines"]
        due_dates = [s.due_date for s in lines if s.due_date]
        entry["due_date"]    = min(due_dates) if due_dates else None
        entry["total_qty"]   = sum((s.qty_ordered or 0) for s in lines)
        entry["total_value"] = sum((s.total_value or 0) for s in lines)
        entry["line_count"]  = len(lines)
        entry["days_delta"]  = (entry["due_date"] - today).days if entry["due_date"] else None

        line_statuses = [s.aggregate_status for s in lines]
        entry["agg_status"] = min(
            line_statuses, key=lambda s: _line_status_priority.get(s, 99)
        )

        # Unique CPRs across all lines (preserving order, excluding nulls)
        entry["cpr_list"] = list(dict.fromkeys(
            sol.customer_product_ref for sol in lines
            if sol.customer_product_ref
        ))

        all_planned = [
            op.planned_date
            for sol in lines
            for op in sol.operations
            if op.planned_date and op.status != WorksOrderOperation.STATUS_CLOSED
        ]
        entry["plan_start"] = min(all_planned) if all_planned else None
        entry["plan_end"]   = max(all_planned) if all_planned else None

        entry["not_started_op_ids"] = [
            op.id
            for sol in lines
            for op in sol.operations
            if op.status == WorksOrderOperation.STATUS_NOT_STARTED
        ]

    total = len(order_list)
    start = (page - 1) * per_page
    page_items = order_list[start: start + per_page]
    return SimplePagination(page_items, total, page, per_page), page_items


# ---------------------------------------------------------------------------
# Status updates
# ---------------------------------------------------------------------------

def update_operation_status(
    operation_id: int,
    new_status: str,
    planned_date: Optional[date] = None,
    notes: Optional[str] = None,
) -> WorksOrderOperation:
    """
    Update the planner fields on a single operation.
    Validates the status transition before saving.
    """
    op = WorksOrderOperation.query.get(operation_id)
    if op is None:
        raise NotFoundError(f"Operation {operation_id} not found")

    if new_status not in WorksOrderOperation.VALID_STATUSES:
        raise ValidationError(f"Invalid status: {new_status}")

    op.status = new_status

    if new_status == WorksOrderOperation.STATUS_COMPLETED and op.completed_date is None:
        op.completed_date = date.today()

    if planned_date is not None:
        op.planned_date = planned_date

    if notes is not None:
        op.notes = notes

    db.session.commit()
    return op


def bulk_update_status(
    operation_ids: list[int],
    new_status: str,
) -> int:
    """
    Bulk-update status for multiple operations at once.
    Returns the count of operations updated.
    """
    if new_status not in WorksOrderOperation.VALID_STATUSES:
        raise ValidationError(f"Invalid status: {new_status}")

    count = 0
    ops = WorksOrderOperation.query.filter(WorksOrderOperation.id.in_(operation_ids)).all()
    for op in ops:
        op.status = new_status
        if new_status == WorksOrderOperation.STATUS_COMPLETED and op.completed_date is None:
            op.completed_date = date.today()
        count += 1

    db.session.commit()
    return count


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

def get_wip_summary() -> dict:
    """
    Return aggregate status counts across all active operations.
    Used for the WIP tracker header banner.
    """
    rows = (
        db.session.query(
            WorksOrderOperation.status,
            func.count(WorksOrderOperation.id),
        )
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .group_by(WorksOrderOperation.status)
        .all()
    )
    counts = {status: count for status, count in rows}
    total = sum(counts.values())
    today = date.today()

    overdue_count = (
        WorksOrderOperation.query
        .filter(
            WorksOrderOperation.due_date < today,
            WorksOrderOperation.status.notin_([
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ])
        )
        .count()
    )

    return {
        "total": total,
        "overdue": overdue_count,
        "by_status": counts,
    }


def get_active_departments() -> list[Department]:
    """Return all active departments, ordered by name."""
    return Department.query.filter_by(is_active=True).order_by(Department.name).all()


# ---------------------------------------------------------------------------
# Date Planning
# ---------------------------------------------------------------------------

def get_planning_list(
    *,
    page: int = 1,
    per_page: int = 50,
    filter_mode: str = "all",
    search: Optional[str] = None,
    dept_id: Optional[int] = None,
):
    """
    Return paginated SO lines enriched with computed planning dates.

    filter_mode:
        'all'       — all open lines
        'overdue'   — ERP due_date < today
        'this_week' — due within next 7 days
        'no_dates'  — open operations with no planned_date set

    Returns (pagination_object, enriched_list) where each enriched item is:
        {
            sol:         SalesOrderLine,
            open_ops:    list[WorksOrderOperation] sorted by dept name,
            plan_start:  date | None  — earliest planned_date across open ops,
            plan_end:    date | None  — latest planned_date across open ops,
            days_delta:  int | None   — due_date − today (negative = overdue),
            headroom:    int | None   — due_date − plan_end (negative = late plan),
        }
    """
    from sqlalchemy.orm import joinedload

    today = date.today()
    week_end = today + timedelta(days=7)

    q = SalesOrderLine.query.options(
        joinedload(SalesOrderLine.operations).joinedload(WorksOrderOperation.department)
    )

    # Always exclude fully-closed lines
    q = q.filter(
        SalesOrderLine.operations.any(
            WorksOrderOperation.status.notin_([
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ])
        )
    )

    if filter_mode == "overdue":
        q = q.filter(SalesOrderLine.due_date < today)
    elif filter_mode == "this_week":
        q = q.filter(
            SalesOrderLine.due_date >= today,
            SalesOrderLine.due_date <= week_end,
        )
    elif filter_mode == "no_dates":
        q = q.filter(
            ~SalesOrderLine.operations.any(
                and_(
                    WorksOrderOperation.planned_date.isnot(None),
                    WorksOrderOperation.status.notin_([
                        WorksOrderOperation.STATUS_COMPLETED,
                        WorksOrderOperation.STATUS_CLOSED,
                    ]),
                )
            )
        )

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(db.or_(
            SalesOrderLine.so_number.ilike(term),
            SalesOrderLine.customer_name.ilike(term),
            SalesOrderLine.product_description.ilike(term),
            SalesOrderLine.customer_order_ref.ilike(term),
            SalesOrderLine.customer_product_ref.ilike(term),
        ))

    if dept_id:
        q = q.filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.department_id == dept_id
            )
        )

    q = q.order_by(SalesOrderLine.due_date.asc().nullslast(), SalesOrderLine.so_number)
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)

    enriched = []
    for sol in pagination.items:
        open_ops = [
            op for op in sol.operations
            if op.status not in (
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            )
        ]
        open_ops.sort(key=lambda op: (op.department.name if op.department else ""))

        planned_dates = [op.planned_date for op in open_ops if op.planned_date]
        plan_start = min(planned_dates) if planned_dates else None
        plan_end   = max(planned_dates) if planned_dates else None

        days_delta = (sol.due_date - today).days if sol.due_date else None
        headroom   = (sol.due_date - plan_end).days if (sol.due_date and plan_end) else None

        enriched.append({
            "sol":        sol,
            "open_ops":   open_ops,
            "plan_start": plan_start,
            "plan_end":   plan_end,
            "days_delta": days_delta,
            "headroom":   headroom,
        })

    return pagination, enriched


def get_planning_grouped(
    *,
    page: int = 1,
    per_page: int = 25,
    filter_mode: str = "all",
    search: Optional[str] = None,
    cust_prod_ref: Optional[str] = None,
    dept_id: Optional[int] = None,
):
    """
    Return planning data grouped by SO number.

    Each item is a dict:
        so_number      str
        customer_name  str
        due_date       date | None   — earliest across lines
        plan_start     date | None   — earliest planned_date across all open ops
        plan_end       date | None   — latest planned_date across all open ops
        days_delta     int | None
        headroom       int | None
        agg_status     str
        line_count     int
        rows           list[dict]   — same structure as get_planning_list rows
    """
    from sqlalchemy.orm import joinedload

    today = date.today()
    week_end = today + timedelta(days=7)

    q = SalesOrderLine.query.options(
        joinedload(SalesOrderLine.operations).joinedload(WorksOrderOperation.department)
    )

    q = q.filter(
        SalesOrderLine.operations.any(
            WorksOrderOperation.status.notin_([
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ])
        )
    )

    if filter_mode == "overdue":
        q = q.filter(SalesOrderLine.due_date < today)
    elif filter_mode == "this_week":
        q = q.filter(
            SalesOrderLine.due_date >= today,
            SalesOrderLine.due_date <= week_end,
        )
    elif filter_mode == "no_dates":
        q = q.filter(
            ~SalesOrderLine.operations.any(
                and_(
                    WorksOrderOperation.planned_date.isnot(None),
                    WorksOrderOperation.status.notin_([
                        WorksOrderOperation.STATUS_COMPLETED,
                        WorksOrderOperation.STATUS_CLOSED,
                    ]),
                )
            )
        )

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(db.or_(
            SalesOrderLine.so_number.ilike(term),
            SalesOrderLine.customer_name.ilike(term),
            SalesOrderLine.product_description.ilike(term),
            SalesOrderLine.customer_order_ref.ilike(term),
            SalesOrderLine.customer_product_ref.ilike(term),
        ))

    if cust_prod_ref:
        q = q.filter(SalesOrderLine.customer_product_ref.ilike(f"%{cust_prod_ref.strip()}%"))

    if dept_id:
        q = q.filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.department_id == dept_id
            )
        )

    q = q.order_by(
        SalesOrderLine.due_date.asc().nullslast(),
        SalesOrderLine.so_number,
        SalesOrderLine.line_number,
    )
    all_lines = q.all()

    _line_status_priority = {
        SalesOrderLine.LINE_STATUS_NEW:          0,
        SalesOrderLine.LINE_STATUS_FIRM_PLANNED: 1,
        SalesOrderLine.LINE_STATUS_WIP:          2,
        SalesOrderLine.LINE_STATUS_COMPLETE:     3,
    }

    # Group by so_number
    seen: dict[str, dict] = {}
    order_list: list[dict] = []

    for sol in all_lines:
        if sol.so_number not in seen:
            entry: dict = {
                "so_number":     sol.so_number,
                "customer_name": sol.customer_name or "",
                "rows":          [],
            }
            seen[sol.so_number] = entry
            order_list.append(entry)

        open_ops = [
            op for op in sol.operations
            if op.status not in (
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            )
        ]
        open_ops.sort(key=lambda op: (op.department.name if op.department else ""))

        planned_dates = [op.planned_date for op in open_ops if op.planned_date]
        plan_start = min(planned_dates) if planned_dates else None
        plan_end   = max(planned_dates) if planned_dates else None
        days_delta = (sol.due_date - today).days if sol.due_date else None
        headroom   = (sol.due_date - plan_end).days if (sol.due_date and plan_end) else None

        seen[sol.so_number]["rows"].append({
            "sol":        sol,
            "open_ops":   open_ops,
            "plan_start": plan_start,
            "plan_end":   plan_end,
            "days_delta": days_delta,
            "headroom":   headroom,
        })

    # Order-level aggregates
    for entry in order_list:
        rows = entry["rows"]
        due_dates      = [r["sol"].due_date for r in rows if r["sol"].due_date]
        all_starts     = [r["plan_start"] for r in rows if r["plan_start"]]
        all_ends       = [r["plan_end"]   for r in rows if r["plan_end"]]
        line_statuses  = [r["sol"].aggregate_status for r in rows]

        entry["due_date"]   = min(due_dates) if due_dates else None
        entry["plan_start"] = min(all_starts) if all_starts else None
        entry["plan_end"]   = max(all_ends)   if all_ends   else None
        entry["line_count"] = len(rows)
        entry["days_delta"] = (entry["due_date"] - today).days if entry["due_date"] else None
        entry["headroom"]   = (
            (entry["due_date"] - entry["plan_end"]).days
            if entry["due_date"] and entry["plan_end"] else None
        )
        entry["agg_status"] = min(
            line_statuses,
            key=lambda s: _line_status_priority.get(s, 99),
        )

        # Unique CPRs across all lines (preserving order, excluding nulls)
        entry["cpr_list"] = list(dict.fromkeys(
            r["sol"].customer_product_ref for r in rows
            if r["sol"].customer_product_ref
        ))

    total = len(order_list)
    start = (page - 1) * per_page
    return SimplePagination(order_list[start: start + per_page], total, page, per_page), order_list[start: start + per_page]


def count_planning_filters() -> dict:
    """Return counts for each filter tab on the planning view."""
    today = date.today()
    week_end = today + timedelta(days=7)

    base = SalesOrderLine.query.filter(
        SalesOrderLine.operations.any(
            WorksOrderOperation.status.notin_([
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ])
        )
    )

    return {
        "all":       base.count(),
        "overdue":   base.filter(SalesOrderLine.due_date < today).count(),
        "this_week": base.filter(
            SalesOrderLine.due_date >= today,
            SalesOrderLine.due_date <= week_end,
        ).count(),
        "no_dates":  base.filter(
            ~SalesOrderLine.operations.any(
                and_(
                    WorksOrderOperation.planned_date.isnot(None),
                    WorksOrderOperation.status.notin_([
                        WorksOrderOperation.STATUS_COMPLETED,
                        WorksOrderOperation.STATUS_CLOSED,
                    ]),
                )
            )
        ).count(),
    }
