from flask import Blueprint

workload_bp = Blueprint("workload", __name__, template_folder="templates")

from . import routes  # noqa: F401, E402
