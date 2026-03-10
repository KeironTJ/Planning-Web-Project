"""Capacity blueprint routes."""

from datetime import date, timedelta

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required

from . import capacity_bp
from . import services
from . import scheduler as sched_service
from .models import RoutingTemplate, RoutingStage, RoutingStageEntry
from app.orders.models import Department
from app.extensions import db
from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError


# ---------------------------------------------------------------------------
# Capacity Dashboard
# ---------------------------------------------------------------------------

@capacity_bp.route("/")
@capacity_bp.route("/dashboard")
@login_required
@permission_required("view_capacity")
def dashboard():
    num_weeks  = request.args.get("weeks", 1, type=int)
    dept_id    = request.args.get("dept", None, type=int)
    view_mode  = request.args.get("view", "chart")
    from_str   = request.args.get("from", "")

    num_weeks = max(1, min(26, num_weeks))

    from_date = date.today()
    if from_str:
        try:
            from_date = date.fromisoformat(from_str)
        except ValueError:
            pass

    prev_from = (from_date - timedelta(weeks=num_weeks)).isoformat()
    next_from = (from_date + timedelta(weeks=num_weeks)).isoformat()

    data          = services.get_capacity_dashboard(from_date, num_weeks=num_weeks, dept_id=dept_id)
    departments   = Department.query.filter_by(is_active=True).order_by(Department.name).all()
    selected_dept = Department.query.get(dept_id) if dept_id else None

    chart_data = [
        {"dept": {"id": d["dept"].id, "name": d["dept"].name}, "rows": d["rows"]}
        for d in data["departments"]
    ]

    return render_template(
        "capacity/dashboard.html",
        title="Capacity Dashboard",
        data=data,
        chart_data=chart_data,
        departments=departments,
        selected_dept=selected_dept,
        num_weeks=num_weeks,
        dept_id=dept_id or "",
        view_mode=view_mode,
        from_date=from_date,
        prev_from=prev_from,
        next_from=next_from,
    )


@capacity_bp.route("/dept/<int:dept_id>/calendar")
@login_required
@permission_required("view_capacity")
def dept_calendar(dept_id: int):
    num_weeks = request.args.get("weeks", 8, type=int)
    num_weeks = max(4, min(16, num_weeks))
    cal = services.get_dept_calendar(dept_id, date.today(), num_weeks=num_weeks)
    return render_template(
        "capacity/dept_calendar.html",
        title=f"{cal['dept'].name} — Labour Plan",
        cal=cal,
        num_weeks=num_weeks,
        departments=Department.query.filter_by(is_active=True).order_by(Department.name).all(),
    )


@capacity_bp.route("/bucket/<int:bucket_id>/override", methods=["POST"])
@login_required
@permission_required("override_capacity")
def override_bucket(bucket_id: int):
    try:
        hours = float(request.form.get("hours", 0))
        services.override_bucket(bucket_id, hours)
        flash("Capacity updated.", "success")
    except (NotFoundError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("capacity.dashboard"))


# ---------------------------------------------------------------------------
# Routing Templates
# ---------------------------------------------------------------------------

@capacity_bp.route("/routing/")
@login_required
@permission_required("override_capacity")
def routing_list():
    templates = RoutingTemplate.query.order_by(RoutingTemplate.name).all()
    return render_template("capacity/routing_list.html", title="Routing Templates", templates=templates)


@capacity_bp.route("/routing/new", methods=["GET", "POST"])
@login_required
@permission_required("override_capacity")
def routing_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name is required.", "danger")
            return redirect(url_for("capacity.routing_new"))
        is_default = bool(request.form.get("is_default"))
        if is_default:
            RoutingTemplate.query.filter_by(is_default=True).update({"is_default": False})
        tmpl = RoutingTemplate(
            name=name,
            description=request.form.get("description", "").strip() or None,
            is_default=is_default,
            is_active=True,
        )
        db.session.add(tmpl)
        db.session.commit()
        flash("Routing template created.", "success")
        return redirect(url_for("capacity.routing_edit", template_id=tmpl.id))
    return render_template("capacity/routing_new.html", title="New Routing Template")


@capacity_bp.route("/routing/<int:template_id>", methods=["GET", "POST"])
@login_required
@permission_required("override_capacity")
def routing_edit(template_id: int):
    tmpl = RoutingTemplate.query.get_or_404(template_id)
    departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_template":
            tmpl.name = request.form.get("name", tmpl.name).strip()
            tmpl.description = request.form.get("description", "").strip() or None
            new_default = bool(request.form.get("is_default"))
            if new_default and not tmpl.is_default:
                RoutingTemplate.query.filter(RoutingTemplate.id != tmpl.id).update({"is_default": False})
            tmpl.is_default = new_default
            db.session.commit()
            flash("Template updated.", "success")

        elif action == "add_stage":
            stage_name = request.form.get("stage_name", "").strip() or None
            seq        = request.form.get("sequence_order", type=int)
            dept_id    = request.form.get("department_id", type=int)
            if seq is None or dept_id is None:
                flash("Sequence order and department are required.", "danger")
            else:
                dept = Department.query.get(dept_id)
                if dept is None:
                    flash("Department not found.", "danger")
                else:
                    existing_stage = next((s for s in tmpl.stages if s.sequence_order == seq), None)
                    if existing_stage is None:
                        existing_stage = RoutingStage(
                            routing_template_id=tmpl.id,
                            name=stage_name,
                            sequence_order=seq,
                        )
                        db.session.add(existing_stage)
                        db.session.flush()
                    if not any(e.department_id == dept_id for e in existing_stage.entries):
                        db.session.add(RoutingStageEntry(
                            routing_stage_id=existing_stage.id,
                            department_id=dept_id,
                        ))
                        db.session.commit()
                        flash(f"{dept.name} added to stage {seq}.", "success")
                    else:
                        db.session.rollback()
                        flash(f"{dept.name} is already in stage {seq}.", "warning")

        elif action == "add_dept_to_stage":
            stage_id = request.form.get("stage_id", type=int)
            dept_id  = request.form.get("department_id", type=int)
            stage    = RoutingStage.query.get(stage_id)
            dept     = Department.query.get(dept_id)
            if stage and dept:
                if not any(e.department_id == dept_id for e in stage.entries):
                    db.session.add(RoutingStageEntry(routing_stage_id=stage_id, department_id=dept_id))
                    db.session.commit()
                    flash(f"{dept.name} added to stage {stage.sequence_order}.", "success")
                else:
                    flash(f"{dept.name} is already in that stage.", "warning")

        return redirect(url_for("capacity.routing_edit", template_id=template_id))

    return render_template("capacity/routing_edit.html", title=f"Routing: {tmpl.name}", tmpl=tmpl, departments=departments)


@capacity_bp.route("/routing/<int:template_id>/delete", methods=["POST"])
@login_required
@permission_required("override_capacity")
def routing_delete(template_id: int):
    tmpl = RoutingTemplate.query.get_or_404(template_id)
    db.session.delete(tmpl)
    db.session.commit()
    flash("Routing template deleted.", "success")
    return redirect(url_for("capacity.routing_list"))


@capacity_bp.route("/routing/stage/<int:stage_id>/delete", methods=["POST"])
@login_required
@permission_required("override_capacity")
def routing_stage_delete(stage_id: int):
    stage = RoutingStage.query.get_or_404(stage_id)
    template_id = stage.routing_template_id
    seq = stage.sequence_order
    db.session.delete(stage)
    db.session.commit()
    flash(f"Stage {seq} removed.", "success")
    return redirect(url_for("capacity.routing_edit", template_id=template_id))


@capacity_bp.route("/routing/entry/<int:entry_id>/delete", methods=["POST"])
@login_required
@permission_required("override_capacity")
def routing_entry_delete(entry_id: int):
    entry       = RoutingStageEntry.query.get_or_404(entry_id)
    template_id = entry.stage.routing_template_id
    dept_name   = entry.department.name if entry.department else "Unknown"
    db.session.delete(entry)
    db.session.commit()
    flash(f"{dept_name} removed from stage.", "success")
    return redirect(url_for("capacity.routing_edit", template_id=template_id))


@capacity_bp.route("/routing/entry/<int:entry_id>/lt", methods=["POST"])
@login_required
@permission_required("override_capacity")
def routing_entry_lt(entry_id: int):
    entry       = RoutingStageEntry.query.get_or_404(entry_id)
    template_id = entry.stage.routing_template_id
    raw = request.form.get("lt_days", "").strip()
    if raw == "":
        entry.lead_time_override_days = None
    else:
        try:
            entry.lead_time_override_days = int(raw)
        except ValueError:
            flash("Lead time must be a whole number of days.", "danger")
            return redirect(url_for("capacity.routing_edit", template_id=template_id))
    db.session.commit()
    flash("Lead time updated.", "success")
    return redirect(url_for("capacity.routing_edit", template_id=template_id))


@capacity_bp.route("/routing/stage/<int:stage_id>/rename", methods=["POST"])
@login_required
@permission_required("override_capacity")
def routing_stage_rename(stage_id: int):
    stage = RoutingStage.query.get_or_404(stage_id)
    template_id = stage.routing_template_id
    stage.name = request.form.get("stage_name", "").strip() or None
    db.session.commit()
    flash("Stage name updated.", "success")
    return redirect(url_for("capacity.routing_edit", template_id=template_id))


# ---------------------------------------------------------------------------
# Backward Scheduler
# ---------------------------------------------------------------------------

@capacity_bp.route("/scheduler/", methods=["GET", "POST"])
@login_required
@permission_required("override_capacity")
def scheduler():
    templates = RoutingTemplate.query.filter_by(is_active=True).order_by(RoutingTemplate.name).all()
    result = None

    if request.method == "POST":
        template_id = request.form.get("template_id", type=int)
        overwrite   = request.form.get("overwrite_manual") == "1"
        try:
            result = sched_service.schedule_orders(overwrite_manual=overwrite, template_id=template_id)
            msg = f"Scheduling complete — {result['scheduled']} operations planned"
            if result["skipped"]:
                msg += f", {result['skipped']} skipped (manual dates preserved)"
            flash(msg + ".", "success")
        except ValueError as exc:
            flash(str(exc), "danger")

    return render_template("capacity/scheduler.html", title="Backward Scheduler", templates=templates, result=result)
