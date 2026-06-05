"""Planning department portal routes."""

from flask import render_template
from flask_login import login_required

from . import planning_bp
from app.core.decorators import permission_required


@planning_bp.route("/")
@planning_bp.route("/dashboard")
@login_required
@permission_required("view_capacity")
def dashboard():
    return render_template("planning/dashboard.html", title="Planning")
