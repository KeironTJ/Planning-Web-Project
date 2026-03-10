"""Materials blueprint routes — Phase 6."""

from datetime import date

from flask import render_template, request
from flask_login import login_required

from . import materials_bp
from . import services
from app.orders.models import Department
from app.core.decorators import permission_required


@materials_bp.route("/")
@login_required
@permission_required("view_materials")
def index():
    summary = services.get_stock_summary()
    return render_template(
        "materials/index.html",
        title="Materials",
        summary=summary,
    )


@materials_bp.route("/shortage")
@login_required
@permission_required("view_materials")
def shortage():
    source        = request.args.get("source", "all")
    dept_filter   = request.args.get("dept", "")
    search        = request.args.get("q", "")
    shortages_only = request.args.get("shortages_only", "1") == "1"
    due_before_str = request.args.get("due_before", "")

    due_before = None
    if due_before_str:
        try:
            due_before = date.fromisoformat(due_before_str)
        except ValueError:
            pass

    data = services.get_shortage_report(
        source=source,
        dept_filter=dept_filter or None,
        search=search or None,
        shortages_only=shortages_only,
        due_before=due_before,
    )
    departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()

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
