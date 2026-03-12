"""Materials blueprint routes — Phase 6."""

from datetime import date, timedelta

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import func

from . import materials_bp
from . import services
from .services import _so_from_works_order
from app.extensions import db
from app.orders.models import Department, SalesOrderLine, WorksOrderOperation
from app.extensions import db
from app.core.decorators import permission_required


@materials_bp.route("/")
@login_required
@permission_required("view_materials")
def index():
    summary = services.get_stock_summary()
    weekly = services.get_weekly_availability_summary(weeks_ahead=12)
    return render_template(
        "materials/index.html",
        title="Materials",
        summary=summary,
        weekly=weekly,
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
    # Extract SO numbers from works_order / order_number (strip 2-char line suffix).
    so_numbers = list({
        so for r in data["rows"]
        for so in [_so_from_works_order(r.works_order or r.order_number)]
        if so
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
        so_from_wo=_so_from_works_order,
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


@materials_bp.route("/aftersales-requirements")
@login_required
@permission_required("view_materials")
def aftersales_requirements():
    from app.materials.models import MaterialRequirementAfterSales
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    query = MaterialRequirementAfterSales.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            MaterialRequirementAfterSales.order_number.ilike(like),
            MaterialRequirementAfterSales.product_code.ilike(like),
            MaterialRequirementAfterSales.customer.ilike(like),
        ))
    rows = query.order_by(MaterialRequirementAfterSales.due_date, MaterialRequirementAfterSales.order_number).paginate(page=page, per_page=50, error_out=False)
    total = MaterialRequirementAfterSales.query.count()
    last = MaterialRequirementAfterSales.query.order_by(MaterialRequirementAfterSales.imported_at.desc()).first()
    return render_template(
        "materials/aftersales_requirements.html",
        title="AfterSales Material Requirements",
        rows=rows, q=q, total=total,
        last_imported=last.imported_at if last else None,
    )


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
