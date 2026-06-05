"""Purchasing department portal routes."""

from flask import render_template
from flask_login import login_required

from . import purchasing_bp
from app.core.decorators import permission_required


@purchasing_bp.route("/")
@purchasing_bp.route("/dashboard")
@login_required
@permission_required("view_materials")
def dashboard():
    return render_template("purchasing/dashboard.html", title="Purchasing")
