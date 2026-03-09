"""Capacity planning blueprint routes."""

from datetime import date, timedelta
from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from . import capacity_bp
from .forms import WorkCentreForm, WorkOrderForm, GenerateBucketsForm
from .services import WorkCentreService, CapacityService, WorkOrderService
from .repository import WorkCentreRepository, RoutingRepository
from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError, ValidationError, CapacityError


@capacity_bp.route("/")
@capacity_bp.route("/dashboard")
@login_required
@permission_required("view_capacity")
def dashboard():
    """Main capacity planning dashboard."""
    today = date.today()
    from_date = today - timedelta(days=today.weekday())   # Start of this week
    to_date = from_date + timedelta(weeks=12)

    utilisation = CapacityService.get_utilisation_summary(from_date, to_date)
    wo_summary = WorkOrderService.get_open_orders_summary()

    return render_template(
        "capacity/dashboard.html",
        title="Capacity Dashboard",
        utilisation=utilisation,
        wo_summary=wo_summary,
        from_date=from_date,
        to_date=to_date,
    )


# ---------------------------------------------------------------------------
# Work Centres
# ---------------------------------------------------------------------------

@capacity_bp.route("/work-centres")
@login_required
@permission_required("view_capacity")
def work_centre_list():
    work_centres = WorkCentreService.list_work_centres()
    return render_template("capacity/work_centre_list.html", title="Work Centres", work_centres=work_centres)


@capacity_bp.route("/work-centres/new", methods=["GET", "POST"])
@login_required
@permission_required("create_capacity")
def work_centre_new():
    form = WorkCentreForm()
    if form.validate_on_submit():
        try:
            WorkCentreService.create_work_centre({
                "code": form.code.data.upper(),
                "name": form.name.data,
                "department": form.department.data,
                "description": form.description.data,
                "hours_per_shift": form.hours_per_shift.data,
                "shifts_per_day": form.shifts_per_day.data,
                "efficiency_pct": form.efficiency_pct.data,
            })
            flash("Work centre created.", "success")
            return redirect(url_for("capacity.work_centre_list"))
        except ValidationError as e:
            flash(str(e), "danger")
    return render_template("capacity/work_centre_form.html", form=form, title="New Work Centre")


@capacity_bp.route("/work-centres/<int:wc_id>", methods=["GET", "POST"])
@login_required
@permission_required("edit_capacity")
def work_centre_edit(wc_id: int):
    try:
        wc = WorkCentreService.get_work_centre(wc_id)
    except NotFoundError as e:
        flash(str(e), "danger")
        return redirect(url_for("capacity.work_centre_list"))

    form = WorkCentreForm(obj=wc)
    if form.validate_on_submit():
        try:
            WorkCentreService.update_work_centre(wc_id, {
                "name": form.name.data,
                "department": form.department.data,
                "description": form.description.data,
                "hours_per_shift": form.hours_per_shift.data,
                "shifts_per_day": form.shifts_per_day.data,
                "efficiency_pct": form.efficiency_pct.data,
            })
            flash("Work centre updated.", "success")
            return redirect(url_for("capacity.work_centre_list"))
        except ValidationError as e:
            flash(str(e), "danger")

    return render_template("capacity/work_centre_form.html", form=form, title="Edit Work Centre", wc=wc)


# ---------------------------------------------------------------------------
# Work Orders
# ---------------------------------------------------------------------------

@capacity_bp.route("/work-orders")
@login_required
@permission_required("view_work_orders")
def work_order_list():
    status = request.args.get("status")
    page = request.args.get("page", 1, type=int)
    pagination = WorkOrderService.list_work_orders(status=status, page=page)
    return render_template(
        "capacity/work_order_list.html",
        title="Work Orders",
        pagination=pagination,
        current_status=status,
    )


@capacity_bp.route("/work-orders/new", methods=["GET", "POST"])
@login_required
@permission_required("create_work_order")
def work_order_new():
    form = WorkOrderForm()
    routings = RoutingRepository.get_all()
    form.routing_id.choices = [(0, "— None —")] + [(r.id, f"{r.code} – {r.name}") for r in routings]

    if form.validate_on_submit():
        try:
            data = {
                "order_number": form.order_number.data,
                "product_code": form.product_code.data.upper(),
                "product_description": form.product_description.data,
                "quantity": form.quantity.data,
                "priority": form.priority.data,
                "planned_start": form.planned_start.data,
                "planned_end": form.planned_end.data,
                "routing_id": form.routing_id.data or None,
                "notes": form.notes.data,
            }
            WorkOrderService.create_work_order(data, created_by_id=current_user.id)
            flash("Work order created.", "success")
            return redirect(url_for("capacity.work_order_list"))
        except ValidationError as e:
            flash(str(e), "danger")

    return render_template("capacity/work_order_form.html", form=form, title="New Work Order")


@capacity_bp.route("/work-orders/<int:wo_id>")
@login_required
@permission_required("view_work_orders")
def work_order_detail(wo_id: int):
    try:
        wo = WorkOrderService.get_work_order(wo_id)
    except NotFoundError as e:
        flash(str(e), "danger")
        return redirect(url_for("capacity.work_order_list"))
    return render_template("capacity/work_order_detail.html", title=f"WO {wo.order_number}", wo=wo)


@capacity_bp.route("/work-orders/<int:wo_id>/release", methods=["POST"])
@login_required
@permission_required("edit_work_order")
def work_order_release(wo_id: int):
    try:
        WorkOrderService.release_work_order(wo_id)
        flash("Work order released to production.", "success")
    except (NotFoundError, ValidationError) as e:
        flash(str(e), "danger")
    return redirect(url_for("capacity.work_order_detail", wo_id=wo_id))


@capacity_bp.route("/work-orders/<int:wo_id>/complete", methods=["POST"])
@login_required
@permission_required("close_work_order")
def work_order_complete(wo_id: int):
    try:
        WorkOrderService.complete_work_order(wo_id)
        flash("Work order marked as completed.", "success")
    except (NotFoundError, ValidationError) as e:
        flash(str(e), "danger")
    return redirect(url_for("capacity.work_order_detail", wo_id=wo_id))


# ---------------------------------------------------------------------------
# Capacity Buckets
# ---------------------------------------------------------------------------

@capacity_bp.route("/buckets/generate", methods=["GET", "POST"])
@login_required
@permission_required("create_capacity")
def generate_buckets():
    form = GenerateBucketsForm()
    work_centres = WorkCentreRepository.get_all()
    form.work_centre_id.choices = [(wc.id, f"{wc.code} – {wc.name}") for wc in work_centres]

    if form.validate_on_submit():
        try:
            buckets = CapacityService.generate_weekly_buckets(
                work_centre_id=form.work_centre_id.data,
                from_date=form.from_date.data,
                weeks=form.weeks.data,
            )
            flash(f"{len(buckets)} capacity buckets generated.", "success")
            return redirect(url_for("capacity.dashboard"))
        except (NotFoundError, ValidationError) as e:
            flash(str(e), "danger")

    return render_template("capacity/generate_buckets.html", form=form, title="Generate Capacity Buckets")


# ---------------------------------------------------------------------------
# AJAX / JSON endpoints (used by charts)
# ---------------------------------------------------------------------------

@capacity_bp.route("/api/utilisation")
@login_required
@permission_required("view_capacity")
def api_utilisation():
    """Return utilisation JSON for dashboard charts."""
    from_date = date.today() - timedelta(days=date.today().weekday())
    to_date = from_date + timedelta(weeks=12)
    data = CapacityService.get_utilisation_summary(from_date, to_date)
    # Convert Decimal to float for JSON serialisation
    for item in data:
        item["total_available"] = float(item["total_available"])
        item["total_allocated"] = float(item["total_allocated"])
    return jsonify(data)
