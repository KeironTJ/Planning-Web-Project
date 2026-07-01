from flask import Blueprint

operations_bp = Blueprint("operations", __name__)

from . import routes  # noqa: F401, E402
