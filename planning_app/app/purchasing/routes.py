"""Purchasing department portal routes."""

from datetime import date, timedelta

from flask import render_template
from flask_login import login_required

from . import purchasing_bp
from app.core.decorators import permission_required


@purchasing_bp.route("/")
@purchasing_bp.route("/dashboard")
@login_required
@permission_required("view_materials")
def dashboard():
    from app.purchasing.materials import services
    po_summary  = services.get_purchasing_dashboard(weeks_ahead=8)
    mat_summary = services.get_stock_summary()
    return render_template(
        "purchasing/dashboard.html",
        title="Purchasing",
        po_summary=po_summary,
        mat_summary=mat_summary,
        today=date.today(),
        timedelta=timedelta,
    )
