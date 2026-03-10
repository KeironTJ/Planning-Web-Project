"""
Orders blueprint routes — WIP tracker, department order lists, firming queue.
"""

from datetime import date

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required

from . import orders_bp
from .forms import OperationStatusForm
from .models import SalesOrderLine, WorksOrderOperation
from . import services
from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError, ValidationError


# ---------------------------------------------------------------------------
# WIP Tracker — main view
# ---------------------------------------------------------------------------

@orders_bp.route("/")
@orders_bp.route("/wip")
@login_required
@permission_required("view_orders")
def wip_tracker():
    dept_filter   = request.args.get("dept", "")
    status_filter = request.args.get("status", "")
    search        = request.args.get("q", "")
    overdue_only  = request.args.get("overdue", "") == "1"
    order_by      = request.args.get("sort", "due_date")
    page          = request.args.get("page", 1, type=int)

    lines = services.get_wip_page(
        page=page,
        per_page=50,
        dept_filter=dept_filter or None,
        status_filter=status_filter or None,
        search=search or None,
        overdue_only=overdue_only,
        order_by=order_by,
    )
    summary      = services.get_wip_summary()
    departments  = services.get_active_departments()
    status_meta  = WorksOrderOperation.STATUS_META

    return render_template(
        "orders/wip_tracker.html",
        title="WIP Tracker",
        lines=lines,
        summary=summary,
        departments=departments,
        status_meta=status_meta,
        dept_filter=dept_filter,
        status_filter=status_filter,
        search=search,
        overdue_only=overdue_only,
        order_by=order_by,
        valid_statuses=WorksOrderOperation.VALID_STATUSES,
        today=date.today(),
    )


# ---------------------------------------------------------------------------
# Department order list
# ---------------------------------------------------------------------------

@orders_bp.route("/dept/<int:dept_id>")
@login_required
@permission_required("view_orders")
def dept_orders(dept_id: int):
    status_filter = request.args.get("status", "")
    search        = request.args.get("q", "")
    page          = request.args.get("page", 1, type=int)

    try:
        dept, operations = services.get_dept_operations(
            dept_id,
            page=page,
            per_page=50,
            status_filter=status_filter or None,
            search=search or None,
        )
    except NotFoundError:
        flash("Department not found.", "warning")
        return redirect(url_for("orders.wip_tracker"))

    status_meta  = WorksOrderOperation.STATUS_META
    form         = OperationStatusForm()
    all_depts    = services.get_active_departments()

    return render_template(
        "orders/dept_orders.html",
        title=f"{dept.name} — Orders",
        dept=dept,
        operations=operations,
        status_meta=status_meta,
        status_filter=status_filter,
        search=search,
        form=form,
        all_depts=all_depts,
        valid_statuses=WorksOrderOperation.VALID_STATUSES,
    )


# ---------------------------------------------------------------------------
# Firming queue
# ---------------------------------------------------------------------------

@orders_bp.route("/firming")
@login_required
@permission_required("view_orders")
def firming_queue():
    page  = request.args.get("page", 1, type=int)
    lines = services.get_firming_queue(page=page)
    departments = services.get_active_departments()
    status_meta = WorksOrderOperation.STATUS_META

    return render_template(
        "orders/firming_queue.html",
        title="Firming Queue",
        lines=lines,
        departments=departments,
        status_meta=status_meta,
        today=date.today(),
    )


# ---------------------------------------------------------------------------
# Operation status update (POST — AJAX or standard form)
# ---------------------------------------------------------------------------

@orders_bp.route("/operations/<int:op_id>/update", methods=["POST"])
@login_required
@permission_required("update_order_status")
def update_operation(op_id: int):
    """Update status / planned_date / notes on a single operation."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    new_status   = request.form.get("status")
    planned_date_str = request.form.get("planned_date", "")
    notes        = request.form.get("notes")

    planned_date = None
    if planned_date_str:
        try:
            planned_date = date.fromisoformat(planned_date_str)
        except ValueError:
            pass

    try:
        op = services.update_operation_status(
            op_id,
            new_status,
            planned_date=planned_date,
            notes=notes,
        )
    except (NotFoundError, ValidationError) as exc:
        if is_ajax:
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "danger")
        return redirect(request.referrer or url_for("orders.wip_tracker"))

    if is_ajax:
        label, colour = WorksOrderOperation.STATUS_META.get(op.status, ("Unknown", "secondary"))
        return jsonify({
            "ok": True,
            "status": op.status,
            "label": label,
            "colour": colour,
        })

    flash("Operation updated.", "success")
    return redirect(request.referrer or url_for("orders.wip_tracker"))


@orders_bp.route("/operations/bulk-update", methods=["POST"])
@login_required
@permission_required("update_order_status")
def bulk_update_operations():
    """Bulk status update from a department or WIP view."""
    ids_raw  = request.form.get("operation_ids", "")
    new_status = request.form.get("status", "")
    back_url   = request.form.get("back_url") or url_for("orders.wip_tracker")

    try:
        op_ids = [int(x) for x in ids_raw.split(",") if x.strip()]
    except ValueError:
        flash("Invalid operation selection.", "danger")
        return redirect(back_url)

    if not op_ids:
        flash("No operations selected.", "warning")
        return redirect(back_url)

    try:
        count = services.bulk_update_status(op_ids, new_status)
        flash(f"{count} operation(s) updated to '{new_status}'.", "success")
    except ValidationError as exc:
        flash(str(exc), "danger")

    return redirect(back_url)


# ---------------------------------------------------------------------------
# Date Planning
# ---------------------------------------------------------------------------

@orders_bp.route("/planning/")
@login_required
@permission_required("manage_orders")
def planning():
    filter_mode = request.args.get("filter", "all")
    search      = request.args.get("q", "")
    dept_id     = request.args.get("dept", None, type=int)
    page        = request.args.get("page", 1, type=int)

    pagination, rows = services.get_planning_list(
        page=page,
        per_page=50,
        filter_mode=filter_mode,
        search=search or None,
        dept_id=dept_id,
    )
    counts      = services.count_planning_filters()
    departments = services.get_active_departments()

    return render_template(
        "orders/planning.html",
        title="Date Planning",
        rows=rows,
        pagination=pagination,
        filter_mode=filter_mode,
        counts=counts,
        search=search,
        dept_id=dept_id or "",
        departments=departments,
        today=date.today(),
        valid_statuses=WorksOrderOperation.VALID_STATUSES,
        STATUS_META=WorksOrderOperation.STATUS_META,
    )


@orders_bp.route("/planning/reschedule", methods=["POST"])
@login_required
@permission_required("manage_orders")
def planning_reschedule():
    """Bulk reschedule selected SO lines using the backward scheduler."""
    from app.capacity.scheduler import schedule_orders

    raw_ids     = request.form.get("sol_ids", "")
    today_floor = request.form.get("today_floor") == "1"
    template_id = request.form.get("template_id", None, type=int)
    back_url    = request.form.get("back_url") or url_for("orders.planning")

    try:
        sol_ids = [int(x) for x in raw_ids.split(",") if x.strip()]
    except ValueError:
        flash("Invalid order selection.", "danger")
        return redirect(back_url)

    if not sol_ids:
        flash("No orders selected.", "warning")
        return redirect(back_url)

    try:
        result = schedule_orders(
            overwrite_manual=True,
            template_id=template_id,
            sol_ids=sol_ids,
            floor_date=date.today() if today_floor else None,
        )
        msg = f"{result['scheduled']} operations rescheduled"
        if result["skipped"]:
            msg += f" ({result['skipped']} skipped)"
        if result["no_due_date"]:
            msg += f"; {result['no_due_date']} orders skipped (no ERP due date)"
        flash(msg + ".", "success")
    except ValueError as exc:
        flash(str(exc), "danger")

    return redirect(back_url)


@orders_bp.route("/planning/clear", methods=["POST"])
@login_required
@permission_required("manage_orders")
def planning_clear_dates():
    """Clear planned_date from all open operations on selected SO lines."""
    raw_ids  = request.form.get("sol_ids", "")
    back_url = request.form.get("back_url") or url_for("orders.planning")

    try:
        sol_ids = [int(x) for x in raw_ids.split(",") if x.strip()]
    except ValueError:
        flash("Invalid order selection.", "danger")
        return redirect(back_url)

    if not sol_ids:
        flash("No orders selected.", "warning")
        return redirect(back_url)

    sols = SalesOrderLine.query.filter(SalesOrderLine.id.in_(sol_ids)).all()
    count = 0
    for sol in sols:
        for op in sol.operations:
            if op.planned_date is not None and op.status not in (
                WorksOrderOperation.STATUS_COMPLETED,
                WorksOrderOperation.STATUS_CLOSED,
            ):
                op.planned_date = None
                count += 1
    from app.extensions import db
    db.session.commit()
    flash(f"Cleared planned dates from {count} operation(s).", "success")
    return redirect(back_url)


@orders_bp.route("/planning/schedule-from-date", methods=["POST"])
@login_required
@permission_required("manage_orders")
def planning_schedule_from_date():
    """
    Schedule selected SO lines from a specific start date.
    Runs the backward scheduler floored to the given date — no operation
    will be planned earlier than floor_date.
    """
    from app.capacity.scheduler import schedule_orders

    raw_ids     = request.form.get("sol_ids", "")
    floor_str   = request.form.get("floor_date", "")
    template_id = request.form.get("template_id", None, type=int)
    back_url    = request.form.get("back_url") or url_for("orders.planning")

    try:
        sol_ids = [int(x) for x in raw_ids.split(",") if x.strip()]
    except ValueError:
        flash("Invalid order selection.", "danger")
        return redirect(back_url)

    if not sol_ids:
        flash("No orders selected.", "warning")
        return redirect(back_url)

    floor_date = None
    if floor_str:
        try:
            floor_date = date.fromisoformat(floor_str)
        except ValueError:
            flash("Invalid date.", "danger")
            return redirect(back_url)

    try:
        result = schedule_orders(
            overwrite_manual=True,
            template_id=template_id,
            sol_ids=sol_ids,
            floor_date=floor_date,
        )
        msg = f"{result['scheduled']} operation(s) scheduled"
        if result["skipped"]:
            msg += f" ({result['skipped']} skipped)"
        if result["no_due_date"]:
            msg += f"; {result['no_due_date']} order(s) skipped (no ERP due date)"
        flash(msg + ".", "success")
    except ValueError as exc:
        flash(str(exc), "danger")

    return redirect(back_url)
