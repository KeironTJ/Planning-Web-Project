"""Forecast preparation and modelling tools within the planning application."""

from flask import Blueprint

forecasting_bp = Blueprint("forecasting", __name__)

from . import routes  # noqa: E402, F401
