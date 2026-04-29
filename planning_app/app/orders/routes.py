"""
Orders blueprint routes — WIP tracker, department order lists.
"""

from datetime import date

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from . import orders_bp
from .forms import OperationStatusForm
from .models import SalesOrderLine, WorksOrderOperation
from . import services
from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError, ValidationError
from app.materials.services import get_so_material_status, MAT_STATUS_META


# ---------------------------------------------------------------------------
# WIP Tracker â€” main view
# ---------------------------------------------------------------------------

@orders_bp.route("/")
def _parse_date(val: str):
    """Parse a YYYY-MM-DD string from a query param; return None if blank/invalid."""
    if not val:
        return None
    try:
        return date.fromisoformat(val)
    except ValueError:
        return None


@orders_bp.route("/dashboard")
@login_required
@permission_required("view_orders")
def wip_dashboard():
    data = services.get_wip_dashboard_data()
    return render_template(
        "orders/wip_dashboard.html",
        title="WIP Dashboard",
        **data,
    )


@orders_bp.route("/overdue-report")
@login_required
@permission_required("view_orders")
def overdue_report():
    dept_id       = request.args.get("dept", None, type=int)
    status_filter = request.args.get("status", "")
    search        = request.args.get("q", "")
    page          = request.args.get("page", 1, type=int)
    per_page      = request.args.get("per_page", 50, type=int)
    sort          = request.args.get("sort", "days_overdue")

    if per_page not in (25, 50, 100):
        per_page = 50
    if sort not in ("days_overdue", "due_date", "value", "customer", "so_number"):
        sort = "days_overdue"

    data        = services.get_overdue_report_data(
        dept_id=dept_id,
        status_filter=status_filter or None,
        search=search or None,
        page=page,
        per_page=per_page,
        sort=sort,
    )
    departments = services.get_active_departments()

    comment_summaries = services.get_comment_summaries([o["so_number"] for o in data["orders"]])
    for o in data["orders"]:
        cs = comment_summaries.get(o["so_number"], {})
        o["comment_count"]  = cs.get("count", 0)
        o["latest_comment"] = cs.get("latest_body")
        o["latest_user"]    = cs.get("latest_user")
        o["latest_at"]      = cs.get("latest_at")

    return render_template(
        "orders/overdue_report.html",
        title="Overdue Orders Report",
        departments=departments,
        dept_id=dept_id or "",
        status_filter=status_filter,
        search=search,
        per_page=per_page,
        sort=sort,
        status_meta=WorksOrderOperation.STATUS_META,
        today=date.today(),
        **data,
    )


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
    per_page      = request.args.get("per_page", 25, type=int)
    if per_page not in (25, 50, 100):
        per_page = 25

    due_date_from    = _parse_date(request.args.get("due_from", ""))
    due_date_to      = _parse_date(request.args.get("due_to", ""))
    planned_date_from = _parse_date(request.args.get("plan_from", ""))
    planned_date_to   = _parse_date(request.args.get("plan_to", ""))
    cust_prod_ref      = request.args.get("cpr", "")
    order_type_filter  = request.args.get("order_type", "")

    pagination, orders = services.get_wip_grouped(
        page=page,
        per_page=per_page,
        dept_filter=dept_filter or None,
        status_filter=status_filter or None,
        search=search or None,
        cust_prod_ref=cust_prod_ref or None,
        overdue_only=overdue_only,
        order_by=order_by,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        planned_date_from=planned_date_from,
        planned_date_to=planned_date_to,
        order_type_filter=order_type_filter or None,
    )
    summary      = services.get_wip_summary()
    departments  = services.get_active_departments()
    order_types  = services.get_order_types()
    status_meta  = WorksOrderOperation.STATUS_META

    # Attach material status to each order on the current page.
    # Use our planned start dates where set â€” materials must arrive by when
    # we actually plan to start the order, not the ERP's MRP date.
    plan_start_map = {
        o["so_number"]: o["plan_start"]
        for o in orders
        if o.get("plan_start")
    }
    mat_status_map = get_so_material_status(
        [o["so_number"] for o in orders],
        plan_start_map=plan_start_map,
    )
    for o in orders:
        o["mat_status"] = mat_status_map.get(o["so_number"], "no_data")

    comment_summaries = services.get_comment_summaries([o["so_number"] for o in orders])
    for o in orders:
        cs = comment_summaries.get(o["so_number"], {})
        o["comment_count"]   = cs.get("count", 0)
        o["latest_comment"]  = cs.get("latest_body")
        o["latest_user"]     = cs.get("latest_user")
        o["latest_at"]       = cs.get("latest_at")

    return render_template(
        "orders/wip_tracker.html",
        title="WIP Tracker",
        pagination=pagination,
        orders=orders,
        summary=summary,
        departments=departments,
        status_meta=status_meta,
        line_status_meta=SalesOrderLine.LINE_STATUS_META,
        mat_status_meta=MAT_STATUS_META,
        dept_filter=dept_filter,
        status_filter=status_filter,
        search=search,
        cust_prod_ref=cust_prod_ref,
        order_type_filter=order_type_filter,
        order_types=order_types,
        overdue_only=overdue_only,
        order_by=order_by,
        per_page=per_page,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        planned_date_from=planned_date_from,
        planned_date_to=planned_date_to,
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
    status_filter  = request.args.get("status", "")
    search         = request.args.get("q", "")
    order_by       = request.args.get("sort", "due_date")
    page           = request.args.get("page", 1, type=int)
    per_page       = request.args.get("per_page", 25, type=int)
    overdue_only       = request.args.get("overdue", "") == "1"
    due_date_from      = _parse_date(request.args.get("due_from", ""))
    due_date_to        = _parse_date(request.args.get("due_to", ""))
    planned_date_from  = _parse_date(request.args.get("plan_from", ""))
    planned_date_to    = _parse_date(request.args.get("plan_to", ""))
    if per_page not in (25, 50, 100):
        per_page = 25

    try:
        dept, pagination, order_groups = services.get_dept_orders_grouped(
            dept_id,
            page=page,
            per_page=per_page,
            status_filter=status_filter or None,
            search=search or None,
            overdue_only=overdue_only,
            order_by=order_by,
            due_date_from=due_date_from,
            due_date_to=due_date_to,
            planned_date_from=planned_date_from,
            planned_date_to=planned_date_to,
        )
    except NotFoundError:
        flash("Department not found.", "warning")
        return redirect(url_for("orders.wip_tracker"))

    status_meta = WorksOrderOperation.STATUS_META
    form        = OperationStatusForm()
    all_depts   = services.get_active_departments()

    plan_start_map = {g["so_number"]: g["dept_planned"] for g in order_groups if g.get("dept_planned")}
    mat_status_map = get_so_material_status([g["so_number"] for g in order_groups], plan_start_map=plan_start_map)
    for g in order_groups:
        g["mat_status"] = mat_status_map.get(g["so_number"], "no_data")

    return render_template(
        "orders/dept_orders.html",
        title=f"{dept.name} â€” Orders",
        dept=dept,
        order_groups=order_groups,
        pagination=pagination,
        status_meta=status_meta,
        mat_status_meta=MAT_STATUS_META,
        status_filter=status_filter,
        search=search,
        order_by=order_by,
        per_page=per_page,
        overdue_only=overdue_only,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        planned_date_from=planned_date_from,
        planned_date_to=planned_date_to,
        form=form,
        all_depts=all_depts,
        valid_statuses=WorksOrderOperation.VALID_STATUSES,
        today=date.today(),
    )


# ---------------------------------------------------------------------------
# Operation status update (POST â€” AJAX or standard form)
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
# Advance all ops for an SO + department to the next status (AJAX)
# ---------------------------------------------------------------------------

@orders_bp.route("/operations/advance-so-dept", methods=["POST"])
@login_required
@permission_required("update_order_status")
def advance_so_dept():
    """AJAX â€” advance every open op for a given SO + work centre to next status."""
    so_number        = request.form.get("so_number", "").strip()
    work_centre_name = request.form.get("work_centre_name", "").strip()

    if not so_number or not work_centre_name:
        return jsonify({"ok": False, "error": "Missing parameters"}), 400

    result = services.advance_so_dept_status(so_number, work_centre_name)
    result["ok"] = True
    return jsonify(result)


@orders_bp.route("/operations/reverse-so-dept", methods=["POST"])
@login_required
@permission_required("update_order_status")
def reverse_so_dept():
    """AJAX â€” step every op for a given SO + work centre back one status."""
    so_number        = request.form.get("so_number", "").strip()
    work_centre_name = request.form.get("work_centre_name", "").strip()

    if not so_number or not work_centre_name:
        return jsonify({"ok": False, "error": "Missing parameters"}), 400

    result = services.reverse_so_dept_status(so_number, work_centre_name)
    result["ok"] = True
    return jsonify(result)


@orders_bp.route("/so/<so_number>/comments", methods=["GET"])
@login_required
@permission_required("view_orders")
def so_comments(so_number: str):
    """AJAX â€” return comments for an SO as JSON."""
    comments = services.get_so_comments(so_number)
    return jsonify({
        "ok": True,
        "comments": [
            {
                "id": c.id,
                "user": c.user.username if c.user else "deleted",
                "body": c.body,
                "created_at": c.created_at.strftime("%d %b %Y %H:%M"),
            }
            for c in comments
        ],
    })


@orders_bp.route("/so/<so_number>/comments", methods=["POST"])
@login_required
@permission_required("update_order_status")
def add_so_comment(so_number: str):
    """AJAX â€” add a comment to an SO."""
    from flask_login import current_user
    body = request.form.get("body", "").strip()
    try:
        comment = services.add_so_comment(so_number, current_user.id, body)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({
        "ok": True,
        "comment": {
            "id": comment.id,
            "user": current_user.username,
            "body": comment.body,
            "created_at": comment.created_at.strftime("%d %b %Y %H:%M"),
        },
    })


@orders_bp.route("/operations/advance-so-all", methods=["POST"])
@login_required
@permission_required("update_order_status")
def advance_so_all():
    """AJAX â€” advance every open op for a given SO (all depts) to next status."""
    so_number = request.form.get("so_number", "").strip()
    if not so_number:
        return jsonify({"ok": False, "error": "Missing parameters"}), 400
    result = services.advance_so_all_status(so_number)
    result["ok"] = True
    return jsonify(result)


@orders_bp.route("/operations/reverse-so-all", methods=["POST"])
@login_required
@permission_required("update_order_status")
def reverse_so_all():
    """AJAX â€” step every op for a given SO (all depts) back one status."""
    so_number = request.form.get("so_number", "").strip()
    if not so_number:
        return jsonify({"ok": False, "error": "Missing parameters"}), 400
    result = services.reverse_so_all_status(so_number)
    result["ok"] = True
    return jsonify(result)


# ---------------------------------------------------------------------------
# Customer Hold â€” AJAX toggle
# ---------------------------------------------------------------------------

@orders_bp.route("/so/<so_number>/hold", methods=["POST"])
@login_required
@permission_required("manage_orders")
def toggle_customer_hold(so_number):
    """
    AJAX endpoint â€” set or clear the customer_hold flag for all lines on an SO.

    Body (JSON): { "action": "set"|"clear", "note": "..." }
    Returns:     { "ok": true, "held": true|false }
    """
    data   = request.get_json(silent=True) or {}
    action = data.get("action", "")
    note   = data.get("note", "")

    if action not in ("set", "clear"):
        return jsonify({"ok": False, "error": "action must be 'set' or 'clear'"}), 400

    try:
        services.toggle_customer_hold(
            so_number=so_number,
            action=action,
            note=note,
            user_id=current_user.id,
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "held": action == "set"})
