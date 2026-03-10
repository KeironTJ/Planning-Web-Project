"""
Orders service layer.

Contains all query and business logic for the WIP tracker and related views.
Routes should call these functions rather than querying models directly.
"""

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, case, and_

from app.extensions import db
from app.core.exceptions import NotFoundError, ValidationError
from .models import Department, SalesOrderLine, WorksOrderOperation


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


def get_firming_queue(page: int = 1, per_page: int = 50):
    """
    Return orders ready for firming: status = not_started, ordered by due date.
    """
    from sqlalchemy.orm import joinedload
    q = (
        SalesOrderLine.query
        .filter(
            SalesOrderLine.operations.any(
                WorksOrderOperation.status == WorksOrderOperation.STATUS_NOT_STARTED
            )
        )
        .options(joinedload(SalesOrderLine.operations))
        .order_by(SalesOrderLine.due_date.asc().nullslast(), SalesOrderLine.so_number)
    )
    return q.paginate(page=page, per_page=per_page, error_out=False)


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
