"""Capacity blueprint routes — Phase 5."""

from datetime import date

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required

from . import capacity_bp
from . import services
from app.orders.models import Department
from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError


@capacity_bp.route("/")
@capacity_bp.route("/dashboard")
@login_required
@permission_required("view_capacity")
def dashboard():
    num_weeks  = request.args.get("weeks", 13, type=int)
    dept_id    = request.args.get("dept", None, type=int)
    view_mode  = request.args.get("view", "chart")  # chart | table

    # Clamp to sensible range
    num_weeks = max(4, min(26, num_weeks))

    data        = services.get_capacity_dashboard(date.today(), num_weeks=num_weeks, dept_id=dept_id)
    departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()
    selected_dept = Department.query.get(dept_id) if dept_id else None

    # Build a JSON-serialisable version for Chart.js (strip ORM objects)
    chart_data = [
        {
            "dept": {"id": d["dept"].id, "name": d["dept"].name},
            "rows": d["rows"],
        }
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
