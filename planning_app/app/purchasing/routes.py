"""Purchasing department portal routes."""

from datetime import date, timedelta

from flask import render_template
from flask_login import login_required

from . import purchasing_bp
from app.core.decorators import permission_required
from app.purchasing.materials.services.dashboard import get_purchasing_dashboard, get_supplier_delivery, get_weekly_so_breakdown
from app.purchasing.materials.services.stock import get_stock_overview, get_stock_summary


@purchasing_bp.route("/")
@purchasing_bp.route("/dashboard")
@login_required
@permission_required("view_purchasing", "view_materials")
def dashboard():
    return render_template("purchasing/dashboard.html", title="Purchasing")


@purchasing_bp.route("/overview")
@login_required
@permission_required("view_purchasing", "view_materials")
def overview():
    po_summary     = get_purchasing_dashboard(weeks_ahead=8)
    mat_summary    = get_stock_summary()
    so_breakdown   = get_weekly_so_breakdown(weeks_ahead=12)
    stock_overview = get_stock_overview()
    return render_template(
        "purchasing/overview.html",
        title="Procurement Dashboard",
        po_summary=po_summary,
        mat_summary=mat_summary,
        so_breakdown=so_breakdown,
        stock_overview=stock_overview,
        today=date.today(),
        timedelta=timedelta,
    )


@purchasing_bp.route("/suppliers")
@login_required
@permission_required("view_purchasing", "view_materials")
def supplier_delivery():
    data = get_supplier_delivery()
    return render_template(
        "purchasing/supplier_delivery.html",
        title="Supplier Delivery",
        data=data,
        today=date.today(),
    )
