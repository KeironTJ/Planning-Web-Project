"""Workload balancing blueprint routes."""

from flask import render_template
from flask_login import login_required

from . import workload_bp
from app.core.decorators import permission_required
from .models import Team, WorkloadAssignment


@workload_bp.route("/")
@login_required
@permission_required("view_capacity")
def dashboard():
    teams = Team.query.filter_by(is_active=True).all()
    return render_template("workload/dashboard.html", title="Workload Balancing", teams=teams)
