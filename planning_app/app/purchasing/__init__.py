from flask import Blueprint

purchasing_bp = Blueprint("purchasing", __name__)

from . import routes  # noqa: F401, E402
