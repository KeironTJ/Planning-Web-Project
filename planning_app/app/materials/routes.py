"""Materials blueprint routes — Phase 6."""

from datetime import date, timedelta

from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from . import materials_bp
from . import services
from app.extensions import db
from app.orders.models import Department, SalesOrderLine, WorksOrderOperation
from app.core.decorators import permission_required


@materials_bp.route("/")
@login_required
@permission_required("view_materials")
def index():
    summary      = services.get_stock_summary()
    weekly       = services.get_weekly_availability_summary(weeks_ahead=12)
    so_breakdown = services.get_weekly_so_breakdown(weeks_ahead=12)
    return render_template(
        "materials/index.html",
        title="Materials",
        summary=summary,
        weekly=weekly,
        so_breakdown=so_breakdown,
        timedelta=timedelta,
    )


@materials_bp.route("/shortage")
@login_required
@permission_required("view_materials")
def shortage():
    source        = request.args.get("source", "all")
    dept_filter   = request.args.get("dept", "")
    search        = request.args.get("q", "")
    # Checkbox fix: unchecked checkboxes don't submit in GET forms.
    # A hidden "0" field is always submitted; the checkbox adds "1" when checked.
    # getlist preserves order — last value wins (checkbox overrides hidden).
    _so_vals = request.args.getlist("shortages_only")
    shortages_only = _so_vals[-1] == "1" if _so_vals else True
    due_before_str = request.args.get("due_before", "")
    due_from_str   = request.args.get("due_from", "")

    due_before = None
    if due_before_str:
        try:
            due_before = date.fromisoformat(due_before_str)
        except ValueError:
            pass

    due_from = None
    if due_from_str:
        try:
            due_from = date.fromisoformat(due_from_str)
        except ValueError:
            pass

    data = services.get_shortage_report(
        source=source,
        dept_filter=dept_filter or None,
        search=search or None,
        shortages_only=shortages_only,
        due_before=due_before,
        due_from=due_from,
    )
    departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()

    # Build plan_start_map: SO number -> earliest planned op date from our system.
    # Use so_number field directly (populated from 'Order' column during import).
    so_numbers = list({
        r.so_number for r in data["rows"]
        if r.so_number
    })
    plan_start_map: dict = {}
    if so_numbers:
        rows = (
            db.session.query(
                SalesOrderLine.so_number,
                func.min(WorksOrderOperation.planned_date),
            )
            .join(WorksOrderOperation, WorksOrderOperation.sales_order_line_id == SalesOrderLine.id)
            .filter(
                SalesOrderLine.so_number.in_(so_numbers),
                WorksOrderOperation.planned_date.isnot(None),
                WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED,
            )
            .group_by(SalesOrderLine.so_number)
            .all()
        )
        plan_start_map = {so: pd for so, pd in rows}

    return render_template(
        "materials/shortage.html",
        title="Material Shortage Report",
        data=data,
        departments=departments,
        source=source,
        dept_filter=dept_filter,
        search=search,
        shortages_only=shortages_only,
        due_before=due_before_str,
        due_from=due_from_str,
        plan_start_map=plan_start_map,
        today=date.today(),
    )


@materials_bp.route("/stock")
@login_required
@permission_required("view_materials")
def stock_list():
    search = request.args.get("q", "")
    page   = request.args.get("page", 1, type=int)
    stock  = services.get_stock_list(search=search or None, page=page)
    return render_template(
        "materials/stock_list.html",
        title="Stock On Hand",
        stock=stock,
        search=search,
    )


@materials_bp.route("/purchase-orders")
@login_required
@permission_required("view_materials")
def po_list():
    search = request.args.get("q", "")
    page   = request.args.get("page", 1, type=int)
    pos    = services.get_po_list(search=search or None, page=page)
    today  = date.today()
    return render_template(
        "materials/po_list.html",
        title="Open Purchase Orders",
        pos=pos,
        search=search,
        today=today,
    )


@materials_bp.route("/main-requirements")
@login_required
@permission_required("view_materials")
def main_requirements():
    from app.materials.models import MaterialRequirementMain
    from sqlalchemy import distinct
    q = request.args.get("q", "").strip()
    f_dept = request.args.get("dept", "").strip()
    page = request.args.get("page", 1, type=int)
    query = MaterialRequirementMain.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            MaterialRequirementMain.works_order.ilike(like),
            MaterialRequirementMain.material_code.ilike(like),
            MaterialRequirementMain.material_description.ilike(like),
        ))
    if f_dept:
        query = query.filter(MaterialRequirementMain.department == f_dept)
    rows = query.order_by(MaterialRequirementMain.due_date, MaterialRequirementMain.works_order).paginate(page=page, per_page=50, error_out=False)
    total = MaterialRequirementMain.query.count()
    last = MaterialRequirementMain.query.order_by(MaterialRequirementMain.imported_at.desc()).first()
    depts = [r[0] for r in db.session.query(distinct(MaterialRequirementMain.department)).filter(MaterialRequirementMain.department.isnot(None)).order_by(MaterialRequirementMain.department).all()]
    return render_template(
        "materials/main_requirements.html",
        title="Main Material Requirements",
        rows=rows, q=q, f_dept=f_dept, depts=depts, total=total,
        last_imported=last.imported_at if last else None,
    )


@materials_bp.route("/exempt", methods=["GET"])
@login_required
@permission_required("manage_imports")
def exempt_materials():
    search = request.args.get("q", "").strip()
    items = services.get_exempt_materials(search=search or None)
    return render_template(
        "materials/exempt_materials.html",
        title="MRP Exempt Materials",
        items=items,
        search=search,
    )


@materials_bp.route("/exempt/add", methods=["POST"])
@login_required
@permission_required("manage_imports")
def exempt_add():
    raw_codes = request.form.get("codes", "")
    reason = request.form.get("reason", "")
    # Accept newline- or comma-separated codes
    codes = [c for part in raw_codes.replace(",", "\n").splitlines() for c in [part.strip()] if c]
    if not codes:
        flash("No material codes entered.", "warning")
        return redirect(url_for("materials.exempt_materials"))
    result = services.add_exemptions(codes, reason=reason or None, user_id=current_user.id)
    flash(
        f"{result['added']} material{'s' if result['added'] != 1 else ''} added to exempt list"
        + (f" ({result['skipped']} already exempt)" if result["skipped"] else "") + ".",
        "success" if result["added"] else "info",
    )
    return redirect(url_for("materials.exempt_materials"))


@materials_bp.route("/exempt/remove", methods=["POST"])
@login_required
@permission_required("manage_imports")
def exempt_remove_bulk():
    raw_codes = request.form.get("codes", "")
    codes = [c for part in raw_codes.replace(",", "\n").splitlines() for c in [part.strip()] if c]
    if not codes:
        flash("No material codes entered.", "warning")
        return redirect(url_for("materials.exempt_materials"))
    deleted = services.remove_exemptions(codes)
    flash(
        f"{deleted} material{'s' if deleted != 1 else ''} removed from exempt list.",
        "success" if deleted else "info",
    )
    return redirect(url_for("materials.exempt_materials"))


@materials_bp.route("/exempt/<string:code>/delete", methods=["POST"])
@login_required
@permission_required("manage_imports")
def exempt_delete(code):
    deleted = services.remove_exemptions([code])
    if deleted:
        flash(f"{code} removed from exempt list.", "success")
    else:
        flash(f"{code} not found in exempt list.", "warning")
    return redirect(url_for("materials.exempt_materials"))


@materials_bp.route("/mrp")
@login_required
@permission_required("view_materials")
def mrp():
    search    = request.args.get("q", "").strip()
    so_number = request.args.get("so", "").strip()
    data = services.get_mrp_pegging(
        search=search or None,
        so_number=so_number or None,
    )
    return render_template(
        "materials/mrp.html",
        title="MRP Pegging",
        data=data,
        search=search,
        so_number=so_number,
    )
