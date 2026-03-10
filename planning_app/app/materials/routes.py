"""Materials blueprint routes — Phase 6."""

from datetime import date

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import func

from . import materials_bp
from . import services
from .services import _so_from_works_order
from app.orders.models import Department, SalesOrderLine, WorksOrderOperation
from app.extensions import db
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
    # Checkbox fix: unchecked checkboxes don't submit in GET forms.
    # A hidden "0" field is always submitted; the checkbox adds "1" when checked.
    # getlist preserves order — last value wins (checkbox overrides hidden).
    _so_vals = request.args.getlist("shortages_only")
    shortages_only = _so_vals[-1] == "1" if _so_vals else True
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
