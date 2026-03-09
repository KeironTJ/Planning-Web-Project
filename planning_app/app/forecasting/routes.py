"""Forecasting blueprint routes."""

from flask import render_template, redirect, url_for, flash
from flask_login import login_required

from . import forecasting_bp
from .services import ForecastService
from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError


@forecasting_bp.route("/")
@login_required
@permission_required("view_forecast")
def dashboard():
    runs = ForecastService.list_runs()
    return render_template("forecasting/dashboard.html", title="Forecasting", runs=runs)


@forecasting_bp.route("/runs/<int:run_id>")
@login_required
@permission_required("view_forecast")
def run_detail(run_id: int):
    try:
        run = ForecastService.get_run(run_id)
    except NotFoundError as e:
        flash(str(e), "danger")
        return redirect(url_for("forecasting.dashboard"))
    return render_template("forecasting/run_detail.html", title=run.name, run=run)
