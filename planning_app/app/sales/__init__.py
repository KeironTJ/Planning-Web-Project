from flask import Blueprint

sales_bp = Blueprint("sales", __name__)

from . import routes  # noqa: F401, E402
