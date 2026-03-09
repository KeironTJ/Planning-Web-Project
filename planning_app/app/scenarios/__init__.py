from flask import Blueprint

scenarios_bp = Blueprint("scenarios", __name__, template_folder="templates")

from . import routes  # noqa: F401, E402
