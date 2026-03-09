"""Scenario modelling blueprint routes."""

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from . import scenarios_bp
from .models import Scenario
from app.extensions import db
from app.core.decorators import permission_required


@scenarios_bp.route("/")
@login_required
@permission_required("view_scenarios")
def dashboard():
    scenarios = Scenario.query.order_by(Scenario.created_at.desc()).all()
    return render_template("scenarios/dashboard.html", title="Scenario Modelling", scenarios=scenarios)


@scenarios_bp.route("/new", methods=["GET", "POST"])
@login_required
@permission_required("create_scenario")
def new_scenario():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "")
        if not name:
            flash("Scenario name is required.", "danger")
        else:
            scenario = Scenario(
                name=name,
                description=description,
                created_by_id=current_user.id,
            )
            db.session.add(scenario)
            db.session.commit()
            flash(f"Scenario '{name}' created.", "success")
            return redirect(url_for("scenarios.dashboard"))
    return render_template("scenarios/new_scenario.html", title="New Scenario")


@scenarios_bp.route("/<int:scenario_id>")
@login_required
@permission_required("view_scenarios")
def scenario_detail(scenario_id: int):
    scenario = Scenario.query.get_or_404(scenario_id)
    return render_template("scenarios/detail.html", title=scenario.name, scenario=scenario)
