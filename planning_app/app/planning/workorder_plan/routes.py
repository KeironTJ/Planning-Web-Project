"""
Works-order capacity planning routes.

URL prefix: /planning/workorder-plan  (registered in app/__init__.py)

Pages
-----
GET  /                              → sessions list
POST /create                        → create session, redirect to workspace
GET  /<id>/workspace                → capacity planning workspace
POST /<id>/override                 → upsert a job override (HTMX or full-page)
POST /<id>/override/remove          → delete a job override
POST /<id>/clone                    → clone session
POST /<id>/delete                   → delete session
GET  /<id>/export.csv               → DMT export CSV (stub)
"""

import io
from datetime import date, timedelta

from flask import (
    Response, flash, redirect, render_template, request, url_for
)
from flask_login import current_user, login_required

from app.core.decorators import permission_required
from app.sales.orders.models import Department

from . import workorder_plan_bp
from . import services
from .services import (
    FILTER_ALL, FILTER_FIRM, FILTER_RELEASED, FILTER_UNFIRM,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_from_date() -> date:
    raw = request.args.get("from", "")
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return date.today()


def _get_filters() -> tuple[str, int, int, str, str]:
    """Return (state_filter, num_weeks, dept_id, measure, from_iso)."""
    state   = request.args.get("state",   FILTER_ALL)
    weeks   = max(1, min(26, request.args.get("weeks", 13, type=int)))
    dept_id = request.args.get("dept", 0, type=int) or None
    measure = request.args.get("measure", "units")
    from_dt = _parse_from_date()
    if state not in (FILTER_FIRM, FILTER_RELEASED, FILTER_ALL, FILTER_UNFIRM):
        state = FILTER_ALL
    if measure not in ("units", "smv"):
        measure = "units"
    return state, weeks, dept_id, measure, from_dt


# ---------------------------------------------------------------------------
# Sessions list
# ---------------------------------------------------------------------------

@workorder_plan_bp.route("/")
@login_required
@permission_required("view_planning")
def sessions():
    all_sessions = services.list_sessions()
    return render_template(
        "planning/workorder_plan/sessions.html",
        title="Planning Sessions",
        sessions=all_sessions,
    )


@workorder_plan_bp.route("/create", methods=["POST"])
@login_required
@permission_required("view_planning")
def create_session():
    name  = request.form.get("name", "").strip()
    desc  = request.form.get("description", "").strip()
    if not name:
        flash("Session name is required.", "danger")
        return redirect(url_for("workorder_plan.sessions"))
    s = services.create_session(name, desc, current_user.id)
    flash(f"Session '{s.name}' created.", "success")
    return redirect(url_for("workorder_plan.workspace", session_id=s.id))


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

@workorder_plan_bp.route("/<int:session_id>/workspace")
@login_required
@permission_required("view_planning")
def workspace(session_id: int):
    state, num_weeks, dept_id, measure, from_date = _get_filters()

    data = services.get_planning_workspace(
        session_id=session_id,
        from_date=from_date,
        num_weeks=num_weeks,
        state_filter=state,
        dept_id=dept_id,
        measure=measure,
    )
    if data["session"] is None:
        flash("Planning session not found.", "warning")
        return redirect(url_for("workorder_plan.sessions"))

    all_departments = (
        Department.query
        .filter_by(is_active=True)
        .order_by(Department.flow_order.nulls_last(), Department.name)
        .all()
    )

    prev_from = (from_date - timedelta(weeks=num_weeks)).isoformat()
    next_from = (from_date + timedelta(weeks=num_weeks)).isoformat()

    from app.purchasing.materials.services.types import MAT_STATUS_META
    order_groups = services.group_orders_by_order_num(data["orders"])

    return render_template(
        "planning/workorder_plan/workspace.html",
        title=f"Planning — {data['session'].name}",
        data=data,
        all_departments=all_departments,
        order_groups=order_groups,
        mat_status_meta=MAT_STATUS_META,
        today=date.today(),
        selected_dept_id=dept_id or "",
        num_weeks=num_weeks,
        state=state,
        measure=measure,
        from_date=from_date,
        prev_from=prev_from,
        next_from=next_from,
    )


# ---------------------------------------------------------------------------
# Override management
# ---------------------------------------------------------------------------

@workorder_plan_bp.route("/<int:session_id>/override", methods=["POST"])
@login_required
@permission_required("view_planning")
def override(session_id: int):
    job_num      = request.form.get("job_num",      "").strip()
    assembly_seq = request.form.get("assembly_seq", 0, type=int)
    plnwk        = request.form.get("override_plnwk",    "").strip() or None
    due_raw      = request.form.get("override_due_date",  "").strip()
    notes        = request.form.get("notes", "").strip() or None

    due_date = None
    if due_raw:
        try:
            due_date = date.fromisoformat(due_raw)
        except ValueError:
            flash("Invalid due date.", "danger")
            return redirect(request.referrer or url_for("workorder_plan.workspace", session_id=session_id))

    if not job_num:
        flash("Job number is required.", "danger")
        return redirect(request.referrer or url_for("workorder_plan.workspace", session_id=session_id))

    services.upsert_override(
        session_id=session_id,
        job_num=job_num,
        assembly_seq=assembly_seq,
        override_plnwk=plnwk,
        override_due_date=due_date,
        notes=notes,
        user_id=current_user.id,
    )
    flash(f"Override saved for job {job_num}.", "success")
    return redirect(request.referrer or url_for("workorder_plan.workspace", session_id=session_id))


@workorder_plan_bp.route("/<int:session_id>/override/remove", methods=["POST"])
@login_required
@permission_required("view_planning")
def remove_override(session_id: int):
    job_num      = request.form.get("job_num",      "").strip()
    assembly_seq = request.form.get("assembly_seq", 0, type=int)
    if job_num:
        services.remove_override(session_id, job_num, assembly_seq)
        flash(f"Override removed for job {job_num}.", "success")
    return redirect(request.referrer or url_for("workorder_plan.workspace", session_id=session_id))


@workorder_plan_bp.route("/<int:session_id>/order-override", methods=["POST"])
@login_required
@permission_required("view_planning")
def order_override(session_id: int):
    """Apply a planning override to ALL jobs belonging to the same sales order."""
    order_num_raw = request.form.get("order_num", "").strip()
    plnwk         = request.form.get("override_plnwk",   "").strip() or None
    due_raw       = request.form.get("override_due_date", "").strip()
    notes         = request.form.get("notes", "").strip() or None

    if not order_num_raw:
        flash("Order number is required.", "danger")
        return redirect(request.referrer or url_for("workorder_plan.workspace", session_id=session_id))

    try:
        order_num = int(order_num_raw)
    except ValueError:
        flash("Invalid order number.", "danger")
        return redirect(request.referrer or url_for("workorder_plan.workspace", session_id=session_id))

    due_date = None
    if due_raw:
        try:
            due_date = date.fromisoformat(due_raw)
        except ValueError:
            flash("Invalid due date.", "danger")
            return redirect(request.referrer or url_for("workorder_plan.workspace", session_id=session_id))

    count = services.upsert_order_override(
        session_id=session_id,
        order_num=order_num,
        override_plnwk=plnwk,
        override_due_date=due_date,
        notes=notes,
        user_id=current_user.id,
    )
    flash(f"Override applied to {count} job(s) on order {order_num}.", "success")
    return redirect(request.referrer or url_for("workorder_plan.workspace", session_id=session_id))


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@workorder_plan_bp.route("/<int:session_id>/clone", methods=["POST"])
@login_required
@permission_required("view_planning")
def clone_session(session_id: int):
    new_name = request.form.get("name", "").strip()
    if not new_name:
        flash("New session name is required.", "danger")
        return redirect(url_for("workorder_plan.sessions"))
    new_s = services.clone_session(session_id, new_name, current_user.id)
    flash(f"Session cloned as '{new_s.name}'.", "success")
    return redirect(url_for("workorder_plan.workspace", session_id=new_s.id))


@workorder_plan_bp.route("/<int:session_id>/delete", methods=["POST"])
@login_required
@permission_required("view_planning")
def delete_session(session_id: int):
    services.delete_session(session_id)
    flash("Session deleted.", "success")
    return redirect(url_for("workorder_plan.sessions"))


# ---------------------------------------------------------------------------
# DMT Export
# ---------------------------------------------------------------------------

@workorder_plan_bp.route("/<int:session_id>/export.csv")
@login_required
@permission_required("view_planning")
def export_csv(session_id: int):
    csv_text = services.build_dmt_export_csv(session_id)
    session  = services.get_session_or_404(session_id)
    filename = f"planning_session_{session_id}_{session.name[:20].replace(' ', '_')}.csv"
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Customer Groups management
# ---------------------------------------------------------------------------

@workorder_plan_bp.route("/groups")
@login_required
@permission_required("view_planning")
def groups_list():
    groups = services.get_all_groups()
    return render_template(
        "planning/workorder_plan/groups.html",
        title="Customer Groups",
        groups=groups,
    )


@workorder_plan_bp.route("/groups/create", methods=["POST"])
@login_required
@permission_required("view_planning")
def groups_create():
    name   = request.form.get("name", "").strip()
    colour = request.form.get("colour", "").strip()
    try:
        sort_order = int(request.form.get("sort_order", "")) if request.form.get("sort_order") else None
    except ValueError:
        sort_order = None
    if not name:
        flash("Group name is required.", "danger")
        return redirect(url_for("workorder_plan.groups_list"))
    g = services.create_group(name, colour, sort_order)
    flash(f"Group '{g.name}' created.", "success")
    return redirect(url_for("workorder_plan.groups_edit", group_id=g.id))


@workorder_plan_bp.route("/groups/<int:group_id>", methods=["GET"])
@login_required
@permission_required("view_planning")
def groups_edit(group_id: int):
    from .models import CustomerGroup
    g = CustomerGroup.query.get_or_404(group_id)
    return render_template(
        "planning/workorder_plan/group_edit.html",
        title=f"Group — {g.name}",
        group=g,
    )


@workorder_plan_bp.route("/groups/<int:group_id>/save", methods=["POST"])
@login_required
@permission_required("view_planning")
def groups_save(group_id: int):
    name      = request.form.get("name", "").strip()
    colour    = request.form.get("colour", "").strip()
    is_active = request.form.get("is_active") == "1"
    try:
        sort_order = int(request.form.get("sort_order", "")) if request.form.get("sort_order") else None
    except ValueError:
        sort_order = None
    if not name:
        flash("Group name is required.", "danger")
        return redirect(url_for("workorder_plan.groups_edit", group_id=group_id))
    services.update_group(group_id, name, colour, sort_order, is_active)
    flash("Group saved.", "success")
    return redirect(url_for("workorder_plan.groups_edit", group_id=group_id))


@workorder_plan_bp.route("/groups/<int:group_id>/members/add", methods=["POST"])
@login_required
@permission_required("view_planning")
def groups_member_add(group_id: int):
    raw = request.form.get("customer_ids", "").strip()
    # Accept comma-separated or one-per-line
    ids = [c.strip() for c in raw.replace("\n", ",").split(",") if c.strip()]
    added = 0
    for cid in ids:
        services.add_group_member(group_id, cid)
        added += 1
    flash(f"{added} customer(s) added.", "success")
    return redirect(url_for("workorder_plan.groups_edit", group_id=group_id))


@workorder_plan_bp.route("/groups/<int:group_id>/members/remove", methods=["POST"])
@login_required
@permission_required("view_planning")
def groups_member_remove(group_id: int):
    customer_id = request.form.get("customer_id", "").strip()
    if customer_id:
        services.remove_group_member(group_id, customer_id)
        flash(f"Customer {customer_id} removed.", "success")
    return redirect(url_for("workorder_plan.groups_edit", group_id=group_id))


@workorder_plan_bp.route("/groups/<int:group_id>/delete", methods=["POST"])
@login_required
@permission_required("view_planning")
def groups_delete(group_id: int):
    services.delete_group(group_id)
    flash("Group deleted.", "success")
    return redirect(url_for("workorder_plan.groups_list"))


# ---------------------------------------------------------------------------
# Capacity targets
# ---------------------------------------------------------------------------
# Capacity — year-grid view (renamed from "targets" to "capacity")
# ---------------------------------------------------------------------------

@workorder_plan_bp.route("/capacity-targets")
@login_required
@permission_required("view_planning")
def capacity_targets():
    today = date.today()
    year  = request.args.get("year", today.year, type=int)
    year  = max(today.year - 2, min(today.year + 5, year))

    default = services.get_default_target()
    grid, groups_for_grid = services.get_year_capacity_grid(year)
    groups  = services.get_all_groups()

    return render_template(
        "planning/workorder_plan/capacity_targets.html",
        title="Capacity Planning",
        year=year,
        prev_year=year - 1,
        next_year=year + 1,
        today_year=today.year,
        today_date=today,
        default=default,
        grid=grid,
        groups=groups,
        groups_for_grid=groups_for_grid,
    )


@workorder_plan_bp.route("/capacity-targets/save-default", methods=["POST"])
@login_required
@permission_required("view_planning")
def capacity_targets_save_default():
    """Save the rolling default (NULL week) target."""
    try:
        target_per_day = float(request.form.get("target_per_day", 0))
        working_days   = int(request.form.get("working_days", 4))
    except ValueError:
        flash("Invalid values.", "danger")
        return redirect(request.referrer or url_for("workorder_plan.capacity_targets"))
    services.save_capacity_target(
        week=None, target_per_day=target_per_day,
        working_days=working_days, max_per_day=None,
        notes=None, user_id=current_user.id,
    )
    flash(f"Default capacity saved: {target_per_day:.0f}/day × {working_days} days = {target_per_day*working_days:.0f} units/week.", "success")
    return redirect(request.referrer or url_for("workorder_plan.capacity_targets"))


@workorder_plan_bp.route("/capacity-targets/save-year", methods=["POST"])
@login_required
@permission_required("view_planning")
def capacity_targets_save_year():
    year = request.form.get("year", date.today().year, type=int)
    weeks_in_year = services.get_iso_weeks_for_year(year)
    labels = {w["label"] for w in weeks_in_year}

    rows = []
    for label in labels:
        tpd_raw  = request.form.get(f"tpd_{label}", "").strip()
        wday_raw = request.form.get(f"wd_{label}", "4").strip()
        if not tpd_raw:
            continue
        try:
            rows.append({
                "week":           label,
                "target_per_day": float(tpd_raw),
                "working_days":   int(wday_raw) if wday_raw else 4,
            })
        except ValueError:
            pass

    saved = services.bulk_save_capacity_targets(rows, current_user.id)

    # Also save per-week group capacities from the same form
    groups = services.get_active_groups()
    group_rows = []
    for label in labels:
        for g in groups:
            gcap_raw = request.form.get(f"gcap_{g.id}_{label}", "").strip()
            if gcap_raw:
                try:
                    group_rows.append({
                        "group_id":        g.id,
                        "week":            label,
                        "weekly_capacity": float(gcap_raw),
                    })
                except ValueError:
                    pass
    gsaved = services.bulk_save_group_week_capacities(group_rows, current_user.id)

    flash(f"Capacity saved — {saved} week override(s), {gsaved} group override(s) for {year}.", "success")
    return redirect(url_for("workorder_plan.capacity_targets", year=year))


@workorder_plan_bp.route("/capacity-targets/save-groups", methods=["POST"])
@login_required
@permission_required("view_planning")
def capacity_targets_save_groups():
    """Save weekly capacity for all customer groups."""
    groups = services.get_all_groups()
    updated = 0
    for g in groups:
        raw = request.form.get(f"cap_{g.id}", "").strip()
        cap = float(raw) if raw else None
        services.save_group_capacity(g.id, cap)
        updated += 1
    flash(f"Group capacities saved for {updated} group(s).", "success")
    return redirect(url_for("workorder_plan.capacity_targets"))


@workorder_plan_bp.route("/capacity-targets/<int:target_id>/delete", methods=["POST"])
@login_required
@permission_required("view_planning")
def capacity_targets_delete(target_id: int):
    services.delete_capacity_target(target_id)
    flash("Capacity override deleted.", "success")
    return redirect(url_for("workorder_plan.capacity_targets"))
