"""
Orders service layer.

Contains all query and business logic for the WIP tracker and related views.
Routes should call these functions rather than querying models directly.
"""

from datetime import date
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
