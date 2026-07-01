from flask import Blueprint

capacity_bp = Blueprint("capacity", __name__, template_folder="templates")

from . import routes  # noqa: F401, E402
