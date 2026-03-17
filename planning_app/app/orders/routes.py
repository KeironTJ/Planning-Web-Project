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
from app.materials.services import get_so_material_status, MAT_STATUS_META


# ---------------------------------------------------------------------------
# WIP Tracker — main view
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
    # Use our planned start dates where set — materials must arrive by when
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
        title=f"{dept.name} — Orders",
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
# Firming queue
# ---------------------------------------------------------------------------

@orders_bp.route("/firming")
@login_required
@permission_required("view_orders")
def firming_queue():
    page          = request.args.get("page", 1, type=int)
    per_page      = request.args.get("per_page", 25, type=int)
    search        = request.args.get("q", "")
    cust_prod_ref = request.args.get("cpr", "")
    overdue_only  = request.args.get("overdue", "") == "1"
    sort          = request.args.get("sort", "due_date")
    due_from      = _parse_date(request.args.get("due_from", ""))
    due_to        = _parse_date(request.args.get("due_to", ""))
    if per_page not in (25, 50, 100):
        per_page = 25

    pagination, order_groups = services.get_firming_queue(
        page=page, per_page=per_page,
        search=search or None,
        cust_prod_ref=cust_prod_ref or None,
        due_from=due_from,
        due_to=due_to,
        overdue_only=overdue_only,
        sort=sort,
    )

    plan_start_map = {g["so_number"]: g["plan_start"] for g in order_groups if g.get("plan_start")}
    mat_status_map = get_so_material_status([g["so_number"] for g in order_groups], plan_start_map=plan_start_map)
    for g in order_groups:
        g["mat_status"] = mat_status_map.get(g["so_number"], "no_data")

    return render_template(
        "orders/firming_queue.html",
        title="Firming Queue",
        order_groups=order_groups,
        pagination=pagination,
        search=search,
        cust_prod_ref=cust_prod_ref,
        overdue_only=overdue_only,
        sort=sort,
        due_from=due_from,
        due_to=due_to,
        per_page=per_page,
        line_status_meta=SalesOrderLine.LINE_STATUS_META,
        mat_status_meta=MAT_STATUS_META,
        today=date.today(),
    )


@orders_bp.route("/releasing")
@login_required
@permission_required("view_orders")
def releasing_queue():
    page          = request.args.get("page", 1, type=int)
    per_page      = request.args.get("per_page", 25, type=int)
    search        = request.args.get("q", "")
    cust_prod_ref = request.args.get("cpr", "")
    overdue_only  = request.args.get("overdue", "") == "1"
    sort          = request.args.get("sort", "due_date")
    due_from      = _parse_date(request.args.get("due_from", ""))
    due_to        = _parse_date(request.args.get("due_to", ""))
    if per_page not in (25, 50, 100):
        per_page = 25

    pagination, order_groups = services.get_releasing_queue(
        page=page, per_page=per_page,
        search=search or None,
        cust_prod_ref=cust_prod_ref or None,
        due_from=due_from,
        due_to=due_to,
        overdue_only=overdue_only,
        sort=sort,
    )

    plan_start_map = {g["so_number"]: g["plan_start"] for g in order_groups if g.get("plan_start")}
    mat_status_map = get_so_material_status([g["so_number"] for g in order_groups], plan_start_map=plan_start_map)
    for g in order_groups:
        g["mat_status"] = mat_status_map.get(g["so_number"], "no_data")

    return render_template(
        "orders/releasing_queue.html",
        title="Releasing Queue",
        order_groups=order_groups,
        pagination=pagination,
        search=search,
        cust_prod_ref=cust_prod_ref,
        overdue_only=overdue_only,
        sort=sort,
        due_from=due_from,
        due_to=due_to,
        per_page=per_page,
        line_status_meta=SalesOrderLine.LINE_STATUS_META,
        mat_status_meta=MAT_STATUS_META,
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
# Advance all ops for an SO + department to the next status (AJAX)
# ---------------------------------------------------------------------------

@orders_bp.route("/operations/advance-so-dept", methods=["POST"])
@login_required
@permission_required("update_order_status")
def advance_so_dept():
    """AJAX — advance every open op for a given SO + work centre to next status."""
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
    """AJAX — step every op for a given SO + work centre back one status."""
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
    """AJAX — return comments for an SO as JSON."""
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
    """AJAX — add a comment to an SO."""
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
    """AJAX — advance every open op for a given SO (all depts) to next status."""
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
    """AJAX — step every op for a given SO (all depts) back one status."""
    so_number = request.form.get("so_number", "").strip()
    if not so_number:
        return jsonify({"ok": False, "error": "Missing parameters"}), 400
    result = services.reverse_so_all_status(so_number)
    result["ok"] = True
    return jsonify(result)


# ---------------------------------------------------------------------------
# Date Planning
# ---------------------------------------------------------------------------

@orders_bp.route("/planning/")
@login_required
@permission_required("manage_orders")
def planning():
    filter_mode   = request.args.get("filter", "all")
    search        = request.args.get("q", "")
    dept_id       = request.args.get("dept", None, type=int)
    page          = request.args.get("page", 1, type=int)
    cust_prod_ref = request.args.get("cpr", "")
    sort_by       = request.args.get("sort", "due_date")
    status_filter = request.args.get("status", "")

    if sort_by not in ("due_date", "plan_end", "headroom", "customer", "status"):
        sort_by = "due_date"

    per_page    = request.args.get("per_page", 25, type=int)
    if per_page not in (25, 50, 100):
        per_page = 25

    pagination, order_groups = services.get_planning_grouped(
        page=page,
        per_page=per_page,
        filter_mode=filter_mode,
        search=search or None,
        cust_prod_ref=cust_prod_ref or None,
        dept_id=dept_id,
        sort_by=sort_by,
        status_filter=status_filter or None,
    )
    counts      = services.count_planning_filters()
    departments = services.get_active_departments()

    plan_start_map = {g["so_number"]: g["plan_start"] for g in order_groups if g.get("plan_start")}
    mat_status_map = get_so_material_status([g["so_number"] for g in order_groups], plan_start_map=plan_start_map)
    for g in order_groups:
        g["mat_status"] = mat_status_map.get(g["so_number"], "no_data")

    comment_summaries = services.get_comment_summaries([g["so_number"] for g in order_groups])
    for g in order_groups:
        cs = comment_summaries.get(g["so_number"], {})
        g["comment_count"]  = cs.get("count", 0)
        g["latest_comment"] = cs.get("latest_body")
        g["latest_user"]    = cs.get("latest_user")
        g["latest_at"]      = cs.get("latest_at")

    return render_template(
        "orders/planning.html",
        title="Date Planning",
        order_groups=order_groups,
        pagination=pagination,
        filter_mode=filter_mode,
        counts=counts,
        search=search,
        cust_prod_ref=cust_prod_ref,
        dept_id=dept_id or "",
        per_page=per_page,
        departments=departments,
        today=date.today(),
        valid_statuses=WorksOrderOperation.VALID_STATUSES,
        STATUS_META=WorksOrderOperation.STATUS_META,
        LINE_STATUS_META=SalesOrderLine.LINE_STATUS_META,
        LINE_STATUS_META_ITEMS=list(SalesOrderLine.LINE_STATUS_META.items()),
        mat_status_meta=MAT_STATUS_META,
        sort_by=sort_by,
        status_filter=status_filter,
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
