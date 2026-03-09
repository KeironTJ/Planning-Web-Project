from flask import Blueprint

forecasting_bp = Blueprint("forecasting", __name__, template_folder="templates")

from . import routes  # noqa: F401, E402
