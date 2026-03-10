"""
Orders blueprint routes — WIP tracker, department order lists, firming queue.
"""

from datetime import date

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required

from . import orders_bp
from .forms import OperationStatusForm
from .models import WorksOrderOperation
from . import services
from app.core.exceptions import NotFoundError, ValidationError


# ---------------------------------------------------------------------------
# WIP Tracker — main view
# ---------------------------------------------------------------------------

@orders_bp.route("/")
@orders_bp.route("/wip")
@login_required
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
