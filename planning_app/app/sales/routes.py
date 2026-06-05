"""Sales department portal routes."""

from flask import render_template
from flask_login import login_required

from . import sales_bp
from app.core.decorators import permission_required


@sales_bp.route("/")
@sales_bp.route("/dashboard")
@login_required
@permission_required("view_orders")
def dashboard():
    return render_template("sales/dashboard.html", title="Sales")
