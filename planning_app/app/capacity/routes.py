"""Capacity blueprint routes."""

from datetime import date, timedelta

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from . import capacity_bp
from . import services
from .models import CapacityBucket
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
        title=f"{cal['dept'].name} â€” Labour Plan",
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
            flash("Invalid hours value â€” must be a number.", "danger")
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
        dow = d.weekday()  # 0=Mon â€¦ 6=Sun
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
    flash("Bulk generate complete â€” " + ", ".join(parts) + ".", "success")
    return redirect(url_for(
        "capacity.labour_plan_list",
        from_date=from_str,
        to_date=to_str,
        dept_id=dept_id or "",
    ))
