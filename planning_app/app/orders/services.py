"""
Orders service layer.

Contains all query and business logic for the WIP tracker and related views.
Routes should call these functions rather than querying models directly.
"""

import json
import math
from datetime import date, timedelta
from typing import Optional

from flask_login import current_user
from sqlalchemy import func, case, and_

from app.extensions import db
from app.core.exceptions import NotFoundError, ValidationError
from .models import Department, SalesOrderLine, WorksOrderOperation, SalesOrderComment, SmvMatrix


def _add_working_days(d: date, n: int) -> date:
    """Add n working days (Mon–Fri) to date d, skipping weekends."""
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Mon=0 … Fri=4
            n -= 1
    return d


def _audit(action: str, resource: str, details: dict) -> None:
    """Write a WIP-tracker audit log entry. Never raises."""
    try:
        from app.auth.services import AuthService
        uid = current_user.id if current_user and current_user.is_authenticated else None
        AuthService._log(uid, action, resource, json.dumps(details))
    except Exception:
        pass


# Operation status priority (lowest = least complete = worst)
_STATUS_PRIORITY = {
    WorksOrderOperation.STATUS_NEW_ORDER:    0,
    WorksOrderOperation.STATUS_FIRM_PLANNED: 1,
    WorksOrderOperation.STATUS_RELEASED:     2,
    WorksOrderOperation.STATUS_WIP:          3,
    WorksOrderOperation.STATUS_COMPLETED:    4,
    WorksOrderOperation.STATUS_CLOSED:       5,
}

_NEXT_STATUS = {
    WorksOrderOperation.STATUS_NEW_ORDER:    WorksOrderOperation.STATUS_FIRM_PLANNED,
    WorksOrderOperation.STATUS_FIRM_PLANNED: WorksOrderOperation.STATUS_RELEASED,
    WorksOrderOperation.STATUS_RELEASED:     WorksOrderOperation.STATUS_WIP,
    WorksOrderOperation.STATUS_WIP:          WorksOrderOperation.STATUS_COMPLETED,
    WorksOrderOperation.STATUS_COMPLETED:    WorksOrderOperation.STATUS_COMPLETED,
}

_PREV_STATUS = {
    WorksOrderOperation.STATUS_FIRM_PLANNED: WorksOrderOperation.STATUS_NEW_ORDER,
    WorksOrderOperation.STATUS_RELEASED:     WorksOrderOperation.STATUS_FIRM_PLANNED,
    WorksOrderOperation.STATUS_WIP:          WorksOrderOperation.STATUS_RELEASED,
    WorksOrderOperation.STATUS_COMPLETED:    WorksOrderOperation.STATUS_WIP,
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
    order_type_filter: Optional[str] = None,
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

    if order_type_filter:
        q = q.filter(SalesOrderLine.order_type == order_type_filter)

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
        SalesOrderLine.LINE_STATUS_RELEASED:     2,
        SalesOrderLine.LINE_STATUS_WIP:          3,
        SalesOrderLine.LINE_STATUS_COMPLETED:    4,
    }

    for entry in order_list:
        lines = entry["lines"]

        # Earliest due date
        due_dates = [s.due_date for s in lines if s.due_date]
        entry["due_date"] = min(due_dates) if due_dates else None

        # Total qty and value
        entry["total_qty"] = sum(
            (s.qty_ordered or 0) for s in lines
        )
        entry["total_value"] = sum(
            float(s.total_value or 0) for s in lines
        )
        entry["line_count"] = len(lines)
        entry["op_count"] = sum(
            1 for sol in lines
            for op in sol.operations
            if op.status != WorksOrderOperation.STATUS_CLOSED
        )

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
        entry["plan_date"]  = _add_working_days(max(all_planned), 1) if all_planned else None
        entry["plan_start"] = min(all_planned) if all_planned else None

        # Unique CPRs across all lines (preserving order, excluding nulls)
        entry["cpr_list"] = list(dict.fromkeys(
            sol.customer_product_ref for sol in lines
            if sol.customer_product_ref
        ))

        # Order type — take first line's value (consistent across all lines in an SO)
        entry["order_type"] = lines[0].order_type or "" if lines else ""

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
    _audit("so_dept_advance", f"so:{so_number}", {
        "dept": work_centre_name, "ops_changed": len(ops),
    })

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
    _audit("so_dept_reverse", f"so:{so_number}", {
        "dept": work_centre_name, "ops_changed": len(ops),
    })

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


def advance_so_all_status(so_number: str) -> dict:
    """
    Advance every open operation for a given SO (all work centres) to its next status.
    Returns the new aggregate status across all ops.
    """
    ops = (
        WorksOrderOperation.query
        .filter_by(so_number=so_number)
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
    _audit("so_all_advance", f"so:{so_number}", {"ops_changed": len(ops)})

    remaining = (
        WorksOrderOperation.query
        .filter_by(so_number=so_number)
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .all()
    )
    if not remaining:
        agg = WorksOrderOperation.STATUS_COMPLETED
    else:
        agg = min(remaining, key=lambda o: _STATUS_PRIORITY.get(o.status, 99)).status

    label, colour = WorksOrderOperation.STATUS_META.get(agg, ("Unknown", "secondary"))
    return {"status": agg, "label": label, "colour": colour}


def reverse_so_all_status(so_number: str) -> dict:
    """
    Step every open operation for a given SO (all work centres) back one status.
    Returns the new aggregate status across all ops.
    """
    ops = (
        WorksOrderOperation.query
        .filter_by(so_number=so_number)
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .all()
    )

    for op in ops:
        prev_s = _PREV_STATUS.get(op.status)
        if prev_s is None:
            continue
        op.status = prev_s
        if prev_s != WorksOrderOperation.STATUS_COMPLETED:
            op.completed_date = None

    db.session.commit()
    _audit("so_all_reverse", f"so:{so_number}", {"ops_changed": len(ops)})

    remaining = (
        WorksOrderOperation.query
        .filter_by(so_number=so_number)
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .all()
    )
    if not remaining:
        agg = WorksOrderOperation.STATUS_COMPLETED
    else:
        agg = min(remaining, key=lambda o: _STATUS_PRIORITY.get(o.status, 99)).status

    label, colour = WorksOrderOperation.STATUS_META.get(agg, ("Unknown", "secondary"))
    return {"status": agg, "label": label, "colour": colour}


def get_dept_orders_grouped(
    dept_id: int,
    *,
    page: int = 1,
    per_page: int = 25,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    overdue_only: bool = False,
    order_by: str = "due_date",
    due_date_from: Optional[date] = None,
    due_date_to: Optional[date] = None,
    planned_date_from: Optional[date] = None,
    planned_date_to: Optional[date] = None,
):
    """
    Return (dept, SimplePagination, order_list) for a single department,
    grouped by SO number — same pattern as get_wip_grouped but dept-scoped.

    Each item in order_list:
        so_number    str
        customer_name str
        due_date     date | None   — earliest across lines
        days_delta   int | None
        total_qty    Decimal
        line_count   int
        dept_status  str           — worst op status in this dept
        dept_planned date | None   — earliest planned_date in this dept
        cpr_list     list[str]
        order_type   str
        lines        list[SalesOrderLine]
        dept_ops     list[WorksOrderOperation]  — non-closed ops in this dept
    """
    from sqlalchemy.orm import joinedload

    today = date.today()

    dept = Department.query.get(dept_id)
    if dept is None:
        raise NotFoundError(f"Department {dept_id} not found")

    q = SalesOrderLine.query.options(
        joinedload(SalesOrderLine.operations).joinedload(WorksOrderOperation.department)
    )

    # Must have at least one non-closed op in this dept
    q = q.filter(
        SalesOrderLine.operations.any(
            and_(
                WorksOrderOperation.department_id == dept_id,
                WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED,
            )
        )
    )

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(db.or_(
            SalesOrderLine.so_number.ilike(term),
            SalesOrderLine.customer_name.ilike(term),
            SalesOrderLine.product_code.ilike(term),
            SalesOrderLine.customer_product_ref.ilike(term),
        ))

    if status_filter:
        q = q.filter(
            SalesOrderLine.operations.any(
                and_(
                    WorksOrderOperation.department_id == dept_id,
                    WorksOrderOperation.status == status_filter,
                )
            )
        )

    if overdue_only:
        q = q.filter(
            SalesOrderLine.due_date < today,
            SalesOrderLine.operations.any(
                and_(
                    WorksOrderOperation.department_id == dept_id,
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
                and_(
                    WorksOrderOperation.department_id == dept_id,
                    WorksOrderOperation.planned_date >= planned_date_from,
                )
            )
        )
    if planned_date_to:
        q = q.filter(
            SalesOrderLine.operations.any(
                and_(
                    WorksOrderOperation.department_id == dept_id,
                    WorksOrderOperation.planned_date <= planned_date_to,
                )
            )
        )

    if order_by == "so_number":
        q = q.order_by(SalesOrderLine.so_number, SalesOrderLine.line_number)
    elif order_by == "customer":
        q = q.order_by(
            SalesOrderLine.customer_name,
            SalesOrderLine.due_date.asc().nullslast(),
            SalesOrderLine.so_number,
        )
    else:  # due_date (default)
        q = q.order_by(
            SalesOrderLine.due_date.asc().nullslast(),
            SalesOrderLine.so_number,
            SalesOrderLine.line_number,
        )

    all_lines = q.all()

    # Group by so_number
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

    for entry in order_list:
        lines = entry["lines"]

        due_dates = [s.due_date for s in lines if s.due_date]
        entry["due_date"]   = min(due_dates) if due_dates else None
        entry["days_delta"] = (entry["due_date"] - today).days if entry["due_date"] else None
        entry["total_qty"]  = sum((s.qty_ordered or 0) for s in lines)
        entry["line_count"] = len(lines)
        # op_count set below after dept_ops is built

        # Operations for this dept only (non-closed)
        dept_ops = [
            op
            for sol in lines
            for op in sol.operations
            if op.department_id == dept_id
            and op.status != WorksOrderOperation.STATUS_CLOSED
        ]
        entry["dept_ops"] = dept_ops
        entry["op_count"] = len(dept_ops)

        planned = [op.planned_date for op in dept_ops if op.planned_date]
        entry["dept_planned"] = min(planned) if planned else None

        if dept_ops:
            entry["dept_status"] = min(
                dept_ops, key=lambda o: _STATUS_PRIORITY.get(o.status, 99)
            ).status
        else:
            entry["dept_status"] = WorksOrderOperation.STATUS_COMPLETED

        entry["cpr_list"] = list(dict.fromkeys(
            sol.customer_product_ref for sol in lines if sol.customer_product_ref
        ))
        entry["order_type"] = lines[0].order_type or "" if lines else ""

    if order_by == "plan_date":
        order_list.sort(key=lambda e: e["dept_planned"] or date.max)

    # SMV: pre-load {component_id: smv_minutes} for this dept, then annotate groups
    smv_dict = {
        row.component_id: float(row.smv_minutes)
        for row in SmvMatrix.query.filter_by(department_id=dept_id).all()
        if row.smv_minutes is not None
    }
    for entry in order_list:
        op_smv: dict[int, float] = {}
        total_smv = 0.0
        for op in entry["dept_ops"]:
            sol = op.sales_order_line
            mins = smv_dict.get(sol.product_description or "", 0.0)
            hrs  = round(float(op.qty or 0) * mins / 60, 2)
            op_smv[op.id] = hrs
            total_smv += hrs
        entry["op_smv_hours"]   = op_smv
        entry["dept_smv_hours"] = round(total_smv, 2)

    total = len(order_list)
    start = (page - 1) * per_page
    page_items = order_list[start: start + per_page]

    return dept, SimplePagination(page_items, total, page, per_page), page_items


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


def _build_queue_groups(all_lines: list, page: int, per_page: int, sort: str = "due_date") -> tuple:
    """Shared grouping/aggregation logic for firming and releasing queues."""
    today = date.today()

    _line_status_priority = {
        SalesOrderLine.LINE_STATUS_NEW:          0,
        SalesOrderLine.LINE_STATUS_FIRM_PLANNED: 1,
        SalesOrderLine.LINE_STATUS_RELEASED:     2,
        SalesOrderLine.LINE_STATUS_WIP:          3,
        SalesOrderLine.LINE_STATUS_COMPLETED:    4,
    }

    seen: dict[str, dict] = {}
    order_list: list[dict] = []

    for sol in all_lines:
        if sol.so_number not in seen:
            entry: dict = {"so_number": sol.so_number, "customer_name": sol.customer_name or "", "lines": []}
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
        entry["op_count"]    = sum(
            1 for s in lines
            for op in s.operations
            if op.status != WorksOrderOperation.STATUS_CLOSED
        )
        entry["days_delta"]  = (entry["due_date"] - today).days if entry["due_date"] else None
        entry["agg_status"]  = min(
            [s.aggregate_status for s in lines],
            key=lambda s: _line_status_priority.get(s, 99),
        )
        entry["cpr_list"] = list(dict.fromkeys(
            s.customer_product_ref for s in lines if s.customer_product_ref
        ))
        all_planned = [
            op.planned_date
            for s in lines for op in s.operations
            if op.planned_date and op.status != WorksOrderOperation.STATUS_CLOSED
        ]
        entry["plan_start"] = min(all_planned) if all_planned else None
        entry["plan_end"]   = _add_working_days(max(all_planned), 1) if all_planned else None
        entry["new_order_op_ids"] = [
            op.id for s in lines for op in s.operations
            if op.status == WorksOrderOperation.STATUS_NEW_ORDER
        ]
        entry["firm_planned_op_ids"] = [
            op.id for s in lines for op in s.operations
            if op.status == WorksOrderOperation.STATUS_FIRM_PLANNED
        ]

    # Post-grouping sort
    if sort == "customer":
        order_list.sort(key=lambda e: e["customer_name"].lower())
    elif sort == "so_number":
        order_list.sort(key=lambda e: e["so_number"])
    elif sort == "overdue":
        order_list.sort(key=lambda e: (e["days_delta"] is None, e["days_delta"] if e["days_delta"] is not None else 0))
    # default "due_date" is already ordered by the SQL query

    total = len(order_list)
    start = (page - 1) * per_page
    return SimplePagination(order_list[start: start + per_page], total, page, per_page), order_list[start: start + per_page]


def _queue_query(
    status_filter: list,
    search: Optional[str],
    cust_prod_ref: Optional[str],
    due_from: Optional[date] = None,
    due_to: Optional[date] = None,
    overdue_only: bool = False,
):
    """Base query for queue views — filters by op status, excludes production."""
    from sqlalchemy.orm import joinedload

    production_statuses = [
        WorksOrderOperation.STATUS_RELEASED,
        WorksOrderOperation.STATUS_WIP,
        WorksOrderOperation.STATUS_COMPLETED,
    ]
    q = (
        SalesOrderLine.query
        .filter(SalesOrderLine.operations.any(WorksOrderOperation.status.in_(status_filter)))
        .filter(~SalesOrderLine.operations.any(WorksOrderOperation.status.in_(production_statuses)))
        .options(joinedload(SalesOrderLine.operations).joinedload(WorksOrderOperation.department))
        .order_by(SalesOrderLine.due_date.asc().nullslast(), SalesOrderLine.so_number, SalesOrderLine.line_number)
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
    if due_from:
        q = q.filter(SalesOrderLine.due_date >= due_from)
    if due_to:
        q = q.filter(SalesOrderLine.due_date <= due_to)
    if overdue_only:
        q = q.filter(SalesOrderLine.due_date < date.today())
    return q


def get_firming_queue(
    page: int = 1,
    per_page: int = 25,
    search: Optional[str] = None,
    cust_prod_ref: Optional[str] = None,
    due_from: Optional[date] = None,
    due_to: Optional[date] = None,
    overdue_only: bool = False,
    sort: str = "due_date",
):
    """Orders with new_order operations and no firm_planned/higher ops yet."""
    q = _queue_query([WorksOrderOperation.STATUS_NEW_ORDER], search, cust_prod_ref, due_from, due_to, overdue_only)
    q = q.filter(
        ~SalesOrderLine.operations.any(WorksOrderOperation.status == WorksOrderOperation.STATUS_FIRM_PLANNED)
    )
    return _build_queue_groups(q.all(), page, per_page, sort)


def get_releasing_queue(
    page: int = 1,
    per_page: int = 25,
    search: Optional[str] = None,
    cust_prod_ref: Optional[str] = None,
    due_from: Optional[date] = None,
    due_to: Optional[date] = None,
    overdue_only: bool = False,
    sort: str = "due_date",
):
    """
    Orders that are fully firm planned (no new_order ops remain) and ready for release.
    An order graduates here once all its ops are firm_planned.
    """
    q = _queue_query([WorksOrderOperation.STATUS_FIRM_PLANNED], search, cust_prod_ref, due_from, due_to, overdue_only)
    q = q.filter(
        ~SalesOrderLine.operations.any(WorksOrderOperation.status == WorksOrderOperation.STATUS_NEW_ORDER)
    )
    return _build_queue_groups(q.all(), page, per_page, sort)


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

    old_status = op.status
    op.status = new_status

    if new_status == WorksOrderOperation.STATUS_COMPLETED and op.completed_date is None:
        op.completed_date = date.today()

    if planned_date is not None:
        op.planned_date = planned_date

    if notes is not None:
        op.notes = notes

    db.session.commit()
    _audit("op_status_change", f"operation:{op.id}", {
        "so": op.so_number, "dept": op.work_centre_name,
        "from": old_status, "to": new_status,
    })
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
    Return SO-level counts and values for the WIP tracker summary banner.

    Counts distinct Sales Orders (SO numbers):
      total         — SOs with at least one non-closed op
      overdue       — SOs with at least one overdue open op
      by_status     — for each status, SOs that have at least one op in that status
      total_value   — sum of total_value for all active SOs
      overdue_value — sum of total_value for overdue SOs
      value_by_status — dict of status → sum of total_value
    """
    today = date.today()

    rows = (
        db.session.query(
            WorksOrderOperation.so_number,
            WorksOrderOperation.status,
            WorksOrderOperation.due_date,
        )
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .all()
    )

    seen_sos: set[str]             = set()
    so_statuses: dict[str, set[str]] = {}
    overdue_sos: set[str]          = set()

    for so_number, status, due_date in rows:
        seen_sos.add(so_number)
        so_statuses.setdefault(so_number, set()).add(status)
        if (
            due_date and due_date < today
            and status not in (WorksOrderOperation.STATUS_COMPLETED, WorksOrderOperation.STATUS_CLOSED)
        ):
            overdue_sos.add(so_number)

    # Count distinct SOs per status (each SO counted once per status it appears in)
    by_status: dict[str, int] = {}
    for statuses in so_statuses.values():
        for s in statuses:
            by_status[s] = by_status.get(s, 0) + 1

    # Fetch SO-level value totals for all active SOs in one query
    value_rows = (
        db.session.query(
            SalesOrderLine.so_number,
            func.sum(SalesOrderLine.total_value).label("val"),
        )
        .filter(SalesOrderLine.so_number.in_(seen_sos))
        .group_by(SalesOrderLine.so_number)
        .all()
    )
    so_value_map: dict[str, float] = {r.so_number: float(r.val or 0) for r in value_rows}

    total_value   = sum(so_value_map.values())
    overdue_value = sum(so_value_map.get(so, 0.0) for so in overdue_sos)

    value_by_status: dict[str, float] = {}
    for so_number, statuses in so_statuses.items():
        val = so_value_map.get(so_number, 0.0)
        for s in statuses:
            value_by_status[s] = value_by_status.get(s, 0.0) + val

    return {
        "total":           len(seen_sos),
        "overdue":         len(overdue_sos),
        "by_status":       by_status,
        "total_value":     total_value,
        "overdue_value":   overdue_value,
        "value_by_status": value_by_status,
    }



def get_active_departments() -> list[Department]:
    """Return all active departments, ordered by flow_order (nulls last) then name."""
    return Department.query.filter_by(is_active=True).order_by(
        Department.flow_order.asc().nullslast(), Department.name.asc()
    ).all()


def get_order_types() -> list[str]:
    """Return sorted list of distinct non-null order types from sales order lines."""
    rows = (
        db.session.query(SalesOrderLine.order_type)
        .filter(SalesOrderLine.order_type.isnot(None))
        .distinct()
        .all()
    )
    return sorted(r.order_type for r in rows if r.order_type)


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
        plan_end   = _add_working_days(max(planned_dates), 1) if planned_dates else None

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
    sort_by: str = "due_date",
    status_filter: Optional[str] = None,
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
        SalesOrderLine.LINE_STATUS_RELEASED:     2,
        SalesOrderLine.LINE_STATUS_WIP:          3,
        SalesOrderLine.LINE_STATUS_COMPLETED:    4,
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
        plan_end   = _add_working_days(max(planned_dates), 1) if planned_dates else None
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
        entry["op_count"]   = sum(len(r["open_ops"]) for r in rows)
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

    # Apply status filter (post-aggregation, on agg_status)
    if status_filter:
        order_list = [e for e in order_list if e["agg_status"] == status_filter]

    # Sort the grouped list
    _far_future = date(9999, 12, 31)
    if sort_by == "customer":
        order_list.sort(key=lambda e: e["customer_name"].lower())
    elif sort_by == "plan_end":
        order_list.sort(key=lambda e: (e["plan_end"] is None, e["plan_end"] or _far_future))
    elif sort_by == "headroom":
        order_list.sort(key=lambda e: (e["headroom"] is None, e["headroom"] if e["headroom"] is not None else 9999))
    elif sort_by == "status":
        order_list.sort(key=lambda e: _line_status_priority.get(e["agg_status"], 99))
    else:  # due_date (default)
        order_list.sort(key=lambda e: (e["due_date"] is None, e["due_date"] or _far_future))

    total = len(order_list)
    start = (page - 1) * per_page
    return SimplePagination(order_list[start: start + per_page], total, page, per_page), order_list[start: start + per_page]


def count_planning_filters() -> dict:
    """Return distinct SO counts for each filter tab on the planning view."""
    today = date.today()
    week_end = today + timedelta(days=7)

    excluded = [WorksOrderOperation.STATUS_COMPLETED, WorksOrderOperation.STATUS_CLOSED]

    def _count(*extra_filters):
        q = (
            db.session.query(func.count(func.distinct(WorksOrderOperation.so_number)))
            .join(SalesOrderLine, WorksOrderOperation.sales_order_line_id == SalesOrderLine.id)
            .filter(WorksOrderOperation.status.notin_(excluded))
        )
        for f in extra_filters:
            q = q.filter(f)
        return q.scalar() or 0

    return {
        "all":       _count(),
        "overdue":   _count(SalesOrderLine.due_date < today),
        "this_week": _count(SalesOrderLine.due_date >= today, SalesOrderLine.due_date <= week_end),
        "no_dates":  _count(WorksOrderOperation.planned_date.is_(None)),
    }


# ---------------------------------------------------------------------------
# Sales Order Comments
# ---------------------------------------------------------------------------

def get_comment_summaries(so_numbers: list[str]) -> dict[str, dict]:
    """
    Return comment summary per SO:
      {so_number: {count, latest_body, latest_user, latest_at}}
    """
    if not so_numbers:
        return {}

    from sqlalchemy.orm import aliased
    from app.auth.models import User

    # Count per SO
    count_rows = (
        db.session.query(SalesOrderComment.so_number, func.count(SalesOrderComment.id))
        .filter(SalesOrderComment.so_number.in_(so_numbers))
        .group_by(SalesOrderComment.so_number)
        .all()
    )
    counts = {so: cnt for so, cnt in count_rows}

    # Latest comment per SO (max id = most recent insert)
    max_id_sub = (
        db.session.query(
            SalesOrderComment.so_number,
            func.max(SalesOrderComment.id).label("max_id"),
        )
        .filter(SalesOrderComment.so_number.in_(so_numbers))
        .group_by(SalesOrderComment.so_number)
        .subquery()
    )
    UserAlias = aliased(User)
    latest_rows = (
        db.session.query(SalesOrderComment, UserAlias.username)
        .join(max_id_sub, SalesOrderComment.id == max_id_sub.c.max_id)
        .outerjoin(UserAlias, SalesOrderComment.user_id == UserAlias.id)
        .all()
    )

    result = {so: {"count": cnt, "latest_body": None, "latest_user": None, "latest_at": None}
              for so, cnt in counts.items()}
    for comment, username in latest_rows:
        result[comment.so_number].update({
            "latest_body": comment.body,
            "latest_user": username or "deleted",
            "latest_at":   comment.created_at.strftime("%d %b %H:%M"),
        })
    return result


def get_so_comments(so_number: str) -> list[SalesOrderComment]:
    """Return all comments for an SO, oldest first."""
    return (
        SalesOrderComment.query
        .filter_by(so_number=so_number)
        .order_by(SalesOrderComment.created_at.asc())
        .all()
    )


def add_so_comment(so_number: str, user_id: int, body: str) -> SalesOrderComment:
    """Append a new comment to an SO. Raises ValidationError if body is blank."""
    body = body.strip()
    if not body:
        raise ValidationError("Comment cannot be blank.")
    comment = SalesOrderComment(so_number=so_number, user_id=user_id, body=body)
    db.session.add(comment)
    db.session.commit()
    _audit("so_comment_added", f"so:{so_number}", {"chars": len(body)})
    return comment


# ---------------------------------------------------------------------------
# WIP Dashboard
# ---------------------------------------------------------------------------

def get_wip_dashboard_data() -> dict:
    """
    Return aggregated data for the WIP Dashboard charts and KPIs.

    Returns a dict with keys:
      summary        — from get_wip_summary()
      dept_status    — {depts, statuses, data, colors, labels} for stacked bar
      due_by_week    — {labels, counts} for next 10 weeks + overdue bucket
      throughput     — {labels, counts} completed ops per week, last 8 weeks
      overdue_by_dept — {depts, counts} top depts with overdue ops
    """
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())

    # 1. Summary KPIs
    summary = get_wip_summary()

    # 2. Operations by department + status (horizontal stacked bar)
    dept_status_rows = (
        db.session.query(
            Department.name,
            Department.flow_order,
            WorksOrderOperation.status,
            func.count(WorksOrderOperation.id).label("cnt"),
        )
        .join(Department, WorksOrderOperation.department_id == Department.id)
        .filter(WorksOrderOperation.status.notin_([WorksOrderOperation.STATUS_CLOSED]))
        .group_by(Department.name, Department.flow_order, WorksOrderOperation.status)
        .order_by(
            # nulls last: rows with flow_order=NULL sort after those with a value
            case((Department.flow_order == None, 1), else_=0),
            Department.flow_order.asc(),
            Department.name.asc(),
        )
        .all()
    )

    dept_order = []
    dept_data: dict = {}
    for name, _flow, status, cnt in dept_status_rows:
        if name not in dept_data:
            dept_data[name] = {}
            dept_order.append(name)
        dept_data[name][status] = cnt

    all_statuses = [
        WorksOrderOperation.STATUS_NEW_ORDER,
        WorksOrderOperation.STATUS_FIRM_PLANNED,
        WorksOrderOperation.STATUS_RELEASED,
        WorksOrderOperation.STATUS_WIP,
        WorksOrderOperation.STATUS_COMPLETED,
    ]
    status_colors = {
        "new_order":    "#adb5bd",
        "firm_planned": "#0dcaf0",
        "released":     "#0d6efd",
        "wip":          "#ffc107",
        "completed":    "#198754",
    }

    # 3. Distinct SOs (excl. closed): min due_date for week bucketing
    active_so_due = (
        db.session.query(
            SalesOrderLine.so_number,
            func.min(SalesOrderLine.due_date).label("min_due"),
        )
        .join(WorksOrderOperation, WorksOrderOperation.sales_order_line_id == SalesOrderLine.id)
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .group_by(SalesOrderLine.so_number)
        .all()
    )

    # Best (most advanced) status per SO (excl. closed) — for stacked week chart
    so_status_rows = (
        db.session.query(
            WorksOrderOperation.so_number,
            WorksOrderOperation.status,
        )
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .distinct()
        .all()
    )
    so_status_map: dict = {}
    for so_num, status in so_status_rows:
        if so_num not in so_status_map:
            so_status_map[so_num] = status
        elif _STATUS_PRIORITY.get(status, 99) > _STATUS_PRIORITY.get(so_status_map[so_num], 99):
            so_status_map[so_num] = status

    # Total value per SO (excl. closed) — separate query to avoid join multiplication
    so_value_rows = (
        db.session.query(
            SalesOrderLine.so_number,
            func.sum(SalesOrderLine.total_value).label("total_val"),
        )
        .filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED
            )
        )
        .group_by(SalesOrderLine.so_number)
        .all()
    )
    so_value_map = {so: float(val or 0) for so, val in so_value_rows}
    total_active_value = sum(so_value_map.values())

    # Build week buckets: overdue + next 10 weeks
    week_labels = ["Overdue"]
    week_counts = [0]
    week_values = [0.0]
    week_starts = [None]
    for i in range(10):
        ws = this_monday + timedelta(weeks=i)
        iso_wk = ws.isocalendar()[1]
        week_labels.append(f"Wk {iso_wk}")
        week_counts.append(0)
        week_values.append(0.0)
        week_starts.append(ws)

    n_buckets = len(week_labels)
    week_status_counts: dict = {s: [0] * n_buckets for s in all_statuses}

    for so_num, min_due in active_so_due:
        if min_due is None:
            continue
        val = so_value_map.get(so_num, 0.0)
        agg_status = so_status_map.get(so_num, WorksOrderOperation.STATUS_NEW_ORDER)

        if min_due < this_monday:
            bucket = 0
        else:
            bucket = next(
                (i for i, ws in enumerate(week_starts[1:], 1)
                 if ws <= min_due <= ws + timedelta(days=6)),
                None,
            )

        if bucket is not None:
            week_counts[bucket] += 1
            week_values[bucket] += val
            if agg_status in week_status_counts:
                week_status_counts[agg_status][bucket] += 1

    # 3b. Orders planned by week: max planned_date (+1 working day delivery) per SO
    active_so_planned = (
        db.session.query(
            WorksOrderOperation.so_number,
            func.max(WorksOrderOperation.planned_date).label("max_planned"),
        )
        .filter(
            WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED,
            WorksOrderOperation.planned_date.isnot(None),
        )
        .group_by(WorksOrderOperation.so_number)
        .all()
    )

    plan_week_counts = [0] * n_buckets
    plan_week_values = [0.0] * n_buckets
    plan_week_status_counts: dict = {s: [0] * n_buckets for s in all_statuses}

    for so_num, max_planned in active_so_planned:
        if max_planned is None:
            continue
        delivery_date = _add_working_days(max_planned, 1)
        agg_status = so_status_map.get(so_num, WorksOrderOperation.STATUS_NEW_ORDER)
        val = so_value_map.get(so_num, 0.0)

        if delivery_date < this_monday:
            bucket = 0
        else:
            bucket = next(
                (i for i, ws in enumerate(week_starts[1:], 1)
                 if ws <= delivery_date <= ws + timedelta(days=6)),
                None,
            )

        if bucket is not None:
            plan_week_counts[bucket] += 1
            plan_week_values[bucket] += val
            if agg_status in plan_week_status_counts:
                plan_week_status_counts[agg_status][bucket] += 1

    # 4. Throughput: completed operations per ISO week over last 8 weeks
    eight_weeks_ago = this_monday - timedelta(weeks=8)
    throughput_rows = (
        db.session.query(
            WorksOrderOperation.completed_date,
            func.count(WorksOrderOperation.id).label("cnt"),
        )
        .filter(
            WorksOrderOperation.status == WorksOrderOperation.STATUS_COMPLETED,
            WorksOrderOperation.completed_date >= eight_weeks_ago,
            WorksOrderOperation.completed_date.isnot(None),
        )
        .group_by(WorksOrderOperation.completed_date)
        .all()
    )

    throughput_weeks: dict = {}
    throughput_labels = []
    for i in range(8):
        ws = this_monday - timedelta(weeks=(7 - i))
        iso = ws.isocalendar()
        key = (iso[0], iso[1])
        throughput_weeks[key] = 0
        throughput_labels.append(f"Wk {iso[1]}")

    for completed_date, cnt in throughput_rows:
        if completed_date is None:
            continue
        iso = completed_date.isocalendar()
        key = (iso[0], iso[1])
        if key in throughput_weeks:
            throughput_weeks[key] += cnt

    # 5. Top departments with overdue operations
    overdue_by_dept_rows = (
        db.session.query(
            Department.name,
            func.count(WorksOrderOperation.id).label("cnt"),
        )
        .join(Department, WorksOrderOperation.department_id == Department.id)
        .filter(
            WorksOrderOperation.due_date < today,
            WorksOrderOperation.status.notin_([
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ]),
        )
        .group_by(Department.name)
        .order_by(func.count(WorksOrderOperation.id).desc())
        .limit(10)
        .all()
    )

    # 6. Value by customer (for bar chart on dashboard)
    value_by_customer_rows = (
        db.session.query(
            SalesOrderLine.customer_name,
            func.sum(SalesOrderLine.total_value).label("val"),
        )
        .filter(
            SalesOrderLine.so_number.in_(list(so_value_map.keys())),
            SalesOrderLine.customer_name.isnot(None),
        )
        .group_by(SalesOrderLine.customer_name)
        .order_by(func.sum(SalesOrderLine.total_value).desc())
        .all()
    )
    value_by_customer = [
        {"customer": r.customer_name, "value": float(r.val or 0)}
        for r in value_by_customer_rows
    ]

    return {
        "summary": summary,
        "dept_status": {
            "depts":    dept_order,
            "statuses": all_statuses,
            "data":     {s: [dept_data.get(d, {}).get(s, 0) for d in dept_order] for s in all_statuses},
            "colors":   status_colors,
            "labels":   {s: WorksOrderOperation.STATUS_META.get(s, (s, "secondary"))[0] for s in all_statuses},
        },
        "total_active_value": total_active_value,
        "due_by_week": {
            "labels":        week_labels,
            "counts":        week_counts,
            "values":        [round(v, 2) for v in week_values],
            "status_counts": {s: week_status_counts[s] for s in all_statuses},
            "status_order":  all_statuses,
            "status_labels": {s: WorksOrderOperation.STATUS_META.get(s, (s, "secondary"))[0] for s in all_statuses},
        },
        "planned_by_week": {
            "labels":        week_labels,
            "counts":        plan_week_counts,
            "values":        [round(v, 2) for v in plan_week_values],
            "status_counts": {s: plan_week_status_counts[s] for s in all_statuses},
            "status_order":  all_statuses,
            "status_labels": {s: WorksOrderOperation.STATUS_META.get(s, (s, "secondary"))[0] for s in all_statuses},
        },
        "throughput": {
            "labels": throughput_labels,
            "counts": list(throughput_weeks.values()),
        },
        "overdue_by_dept": {
            "depts":  [r[0] for r in overdue_by_dept_rows],
            "counts": [r[1] for r in overdue_by_dept_rows],
        },
        "value_by_customer": value_by_customer,
    }

