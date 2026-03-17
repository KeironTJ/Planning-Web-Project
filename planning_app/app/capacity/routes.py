"""Capacity blueprint routes."""

from datetime import date, timedelta

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from . import capacity_bp
from . import services
from . import scheduler as sched_service
from .models import RoutingTemplate, RoutingStage, RoutingStageEntry, CapacityBucket
from app.orders.models import Department, SmvMatrix
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
    num_weeks  = request.args.get("weeks", 4, type=int)
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
    departments   = Department.query.filter_by(is_active=True).order_by(Department.flow_order.nulls_last(), Department.name).all()
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


# ---------------------------------------------------------------------------
# SMV Matrix
# ---------------------------------------------------------------------------

@capacity_bp.route("/smv")
@login_required
@permission_required("override_capacity")
def smv_list():
    from math import ceil

    page      = request.args.get("page", 1, type=int)
    search    = request.args.get("q", "").strip()
    f_timing  = request.args.get("timing_code", "").strip()
    f_conf    = request.args.get("confidence", "").strip()
    f_dept_id = request.args.get("dept_id", 0, type=int)

    departments = (
        Department.query
        .filter_by(is_active=True)
        .order_by(Department.flow_order.asc().nullslast(), Department.name.asc())
        .all()
    )

    timing_codes = [
        r[0] for r in
        db.session.query(SmvMatrix.timing_code)
        .filter(SmvMatrix.timing_code.isnot(None))
        .distinct()
        .order_by(SmvMatrix.timing_code)
        .all()
    ]

    cid_q = db.session.query(SmvMatrix.component_id).distinct()
    if search:
        like = f"%{search}%"
        cid_q = cid_q.filter(db.or_(
            SmvMatrix.component_id.ilike(like),
            SmvMatrix.description.ilike(like),
            SmvMatrix.timing_code.ilike(like),
        ))
    if f_timing:
        cid_q = cid_q.filter(SmvMatrix.timing_code == f_timing)
    if f_conf:
        cid_q = cid_q.filter(SmvMatrix.confidence == f_conf)
    if f_dept_id:
        cid_q = cid_q.filter(
            SmvMatrix.department_id == f_dept_id,
            SmvMatrix.smv_minutes > 0,
        )

    matching_cids = sorted([r[0] for r in cid_q.all()])

    all_entries = (
        SmvMatrix.query
        .filter(SmvMatrix.component_id.in_(matching_cids))
        .order_by(SmvMatrix.component_id.asc())
        .all()
    ) if matching_cids else []

    pivot = {}
    for entry in all_entries:
        cid = entry.component_id
        if cid not in pivot:
            pivot[cid] = {
                "component_id": cid,
                "timing_code": entry.timing_code,
                "description": entry.description,
                "cells": {},
            }
        pivot[cid]["cells"][entry.department_id] = entry

    _conf_rank = {
        SmvMatrix.CONFIDENCE_ESTIMATED: 0,
        SmvMatrix.CONFIDENCE_TIMED:     1,
        SmvMatrix.CONFIDENCE_MOST:      2,
    }
    for row in pivot.values():
        row["ops_count"] = sum(
            1 for e in row["cells"].values()
            if e.smv_minutes is not None and e.smv_minutes > 0
        )
        active_cells = [e for e in row["cells"].values() if e.smv_minutes and e.smv_minutes > 0]
        row["confidence"] = (
            min(active_cells, key=lambda e: _conf_rank.get(e.confidence, 0)).confidence
            if active_cells else None
        )
        edited_cells = [e for e in row["cells"].values() if e.last_modified_at]
        if edited_cells:
            latest = max(edited_cells, key=lambda e: e.last_modified_at)
            row["last_modified_at"] = latest.last_modified_at
            row["last_modified_by"] = latest.last_modified_by
        else:
            row["last_modified_at"] = None
            row["last_modified_by"] = None

    total       = len(matching_cids)
    per_page    = 50
    start       = (page - 1) * per_page
    total_pages = ceil(total / per_page) if total else 1
    rows        = [pivot[cid] for cid in matching_cids[start : start + per_page] if cid in pivot]

    return render_template(
        "capacity/smv_list.html",
        title="SMV Matrix",
        rows=rows,
        departments=departments,
        timing_codes=timing_codes,
        search=search,
        f_timing=f_timing,
        f_conf=f_conf,
        f_dept_id=f_dept_id,
        page=page,
        total=total,
        total_pages=total_pages,
        per_page=per_page,
        has_prev=page > 1,
        has_next=page < total_pages,
        confidence_choices=[
            (SmvMatrix.CONFIDENCE_ESTIMATED, "Estimated"),
            (SmvMatrix.CONFIDENCE_TIMED,     "Timed Study"),
            (SmvMatrix.CONFIDENCE_MOST,      "MOST Study"),
        ],
    )


@capacity_bp.route("/smv/<int:smv_id>/edit", methods=["POST"])
@login_required
@permission_required("override_capacity")
def smv_edit(smv_id: int):
    from decimal import Decimal, InvalidOperation
    from datetime import datetime, timezone

    entry = SmvMatrix.query.get_or_404(smv_id)

    smv_minutes = request.form.get("smv_minutes", "").strip()
    confidence  = request.form.get("confidence", "").strip()

    if smv_minutes:
        try:
            entry.smv_minutes = Decimal(smv_minutes)
        except InvalidOperation:
            flash("Invalid SMV value — must be a number.", "danger")
            return redirect(request.referrer or url_for("capacity.smv_list"))
    else:
        entry.smv_minutes = None

    valid_confidences = {
        SmvMatrix.CONFIDENCE_ESTIMATED,
        SmvMatrix.CONFIDENCE_TIMED,
        SmvMatrix.CONFIDENCE_MOST,
    }
    if confidence in valid_confidences:
        entry.confidence = confidence

    entry.last_modified_at = datetime.now(timezone.utc)
    entry.last_modified_by_id = current_user.id

    db.session.commit()
    flash(f"SMV updated for {entry.component_id} / {entry.department.name}.", "success")
    return redirect(request.referrer or url_for("capacity.smv_list"))


# ---------------------------------------------------------------------------
# Labour Plan
# ---------------------------------------------------------------------------

@capacity_bp.route("/labour-plan")
@login_required
@permission_required("override_capacity")
def labour_plan_list():
    today = date.today()
    default_from = today - timedelta(days=today.weekday())
    default_to = default_from + timedelta(weeks=8) - timedelta(days=1)

    from_str  = request.args.get("from_date", default_from.isoformat())
    to_str    = request.args.get("to_date", default_to.isoformat())
    f_dept_id = request.args.get("dept_id", 0, type=int)

    try:
        from_date = date.fromisoformat(from_str)
        to_date   = date.fromisoformat(to_str)
    except ValueError:
        from_date, to_date = default_from, default_to

    if (to_date - from_date).days > 365:
        to_date = from_date + timedelta(days=365)

    all_departments = (
        Department.query
        .filter_by(is_active=True)
        .order_by(Department.flow_order.asc().nullslast(), Department.name.asc())
        .all()
    )
    display_depts = [d for d in all_departments if d.id == f_dept_id] if f_dept_id else all_departments

    bucket_q = CapacityBucket.query.filter(
        CapacityBucket.date >= from_date,
        CapacityBucket.date <= to_date,
    )
    if f_dept_id:
        bucket_q = bucket_q.filter(CapacityBucket.department_id == f_dept_id)
    buckets = bucket_q.order_by(CapacityBucket.date).all()

    pivot = {}
    for b in buckets:
        pivot.setdefault(b.date, {})[b.department_id] = b

    all_dates = []
    d = from_date
    while d <= to_date:
        all_dates.append(d)
        d += timedelta(days=1)

    return render_template(
        "capacity/labour_plan.html",
        title="Labour Plan",
        departments=display_depts,
        all_departments=all_departments,
        all_dates=all_dates,
        pivot=pivot,
        from_date=from_date,
        to_date=to_date,
        f_dept_id=f_dept_id,
    )


@capacity_bp.route("/labour-plan/save", methods=["POST"])
@login_required
@permission_required("override_capacity")
def labour_plan_save():
    from decimal import Decimal, InvalidOperation

    bucket_id           = request.form.get("bucket_id", type=int)
    dept_id             = request.form.get("dept_id", type=int)
    date_str            = request.form.get("date", "").strip()
    available_hours_str = request.form.get("available_hours", "").strip()
    is_workday          = request.form.get("is_workday") == "1"
    day_complete        = request.form.get("day_complete") == "1"

    if bucket_id:
        bucket = CapacityBucket.query.get_or_404(bucket_id)
    else:
        if not dept_id or not date_str:
            flash("Department and date are required.", "danger")
            return redirect(request.referrer or url_for("capacity.labour_plan_list"))
        try:
            entry_date = date.fromisoformat(date_str)
        except ValueError:
            flash("Invalid date.", "danger")
            return redirect(request.referrer or url_for("capacity.labour_plan_list"))
        bucket = CapacityBucket.query.filter_by(department_id=dept_id, date=entry_date).first()
        if not bucket:
            iso = entry_date.isocalendar()
            bucket = CapacityBucket(
                department_id=dept_id,
                date=entry_date,
                week=f"{iso[0]}-W{iso[1]:02d}",
            )
            db.session.add(bucket)

    if available_hours_str:
        try:
            bucket.available_hours = Decimal(available_hours_str)
        except InvalidOperation:
            flash("Invalid hours value — must be a number.", "danger")
            return redirect(request.referrer or url_for("capacity.labour_plan_list"))
    else:
        bucket.available_hours = None

    bucket.is_workday    = is_workday
    bucket.day_complete  = day_complete
    bucket.manually_overridden = True

    if bucket.date:
        iso = bucket.date.isocalendar()
        bucket.week = f"{iso[0]}-W{iso[1]:02d}"

    db.session.commit()
    flash("Labour plan entry saved.", "success")
    return redirect(request.referrer or url_for("capacity.labour_plan_list"))


@capacity_bp.route("/labour-plan/bulk-generate", methods=["POST"])
@login_required
@permission_required("override_capacity")
def labour_plan_bulk_generate():
    from decimal import Decimal, InvalidOperation

    dept_id      = request.form.get("dept_id", 0, type=int)
    from_str     = request.form.get("from_date", "").strip()
    to_str       = request.form.get("to_date", "").strip()
    skip_manual  = request.form.get("skip_manual") == "1"

    # Per-day hours and workday flags
    day_hours = {}
    day_workday = {}
    day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    for i, name in enumerate(day_names):
        raw = request.form.get(f"hours_{name}", "").strip()
        try:
            day_hours[i] = Decimal(raw) if raw else None
        except InvalidOperation:
            flash(f"Invalid hours value for {name.capitalize()}.", "danger")
            return redirect(url_for("capacity.labour_plan_list"))
        day_workday[i] = request.form.get(f"workday_{name}") == "1"

    try:
        from_date = date.fromisoformat(from_str)
        to_date   = date.fromisoformat(to_str)
    except ValueError:
        flash("Invalid date range.", "danger")
        return redirect(url_for("capacity.labour_plan_list"))

    if to_date < from_date:
        flash("To date must be on or after from date.", "danger")
        return redirect(url_for("capacity.labour_plan_list"))

    if (to_date - from_date).days > 365:
        flash("Date range cannot exceed 1 year.", "danger")
        return redirect(url_for("capacity.labour_plan_list"))

    departments = (
        [Department.query.get_or_404(dept_id)] if dept_id
        else Department.query.filter_by(is_active=True).all()
    )

    # Pre-load existing buckets into a set for fast lookup
    existing = {
        (b.department_id, b.date): b
        for b in CapacityBucket.query.filter(
            CapacityBucket.date >= from_date,
            CapacityBucket.date <= to_date,
            CapacityBucket.department_id.in_([d.id for d in departments]),
        ).all()
    }

    created = updated = skipped = 0
    d = from_date
    while d <= to_date:
        dow = d.weekday()  # 0=Mon … 6=Sun
        iso = d.isocalendar()
        week_str = f"{iso[0]}-W{iso[1]:02d}"
        hours = day_hours[dow]
        is_workday = day_workday[dow]

        for dept in departments:
            key = (dept.id, d)
            bucket = existing.get(key)
            if bucket:
                if skip_manual and bucket.manually_overridden:
                    skipped += 1
                    continue
                bucket.available_hours     = hours
                bucket.is_workday          = is_workday
                bucket.manually_overridden = True
                bucket.week                = week_str
                updated += 1
            else:
                bucket = CapacityBucket(
                    department_id=dept.id,
                    date=d,
                    week=week_str,
                    available_hours=hours,
                    is_workday=is_workday,
                    manually_overridden=True,
                )
                db.session.add(bucket)
                created += 1

        d += timedelta(days=1)

    db.session.commit()

    parts = []
    if created:
        parts.append(f"{created} created")
    if updated:
        parts.append(f"{updated} updated")
    if skipped:
        parts.append(f"{skipped} skipped (manual)")
    flash("Bulk generate complete — " + ", ".join(parts) + ".", "success")
    return redirect(url_for(
        "capacity.labour_plan_list",
        from_date=from_str,
        to_date=to_str,
        dept_id=dept_id or "",
    ))
