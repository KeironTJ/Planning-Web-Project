from flask import Blueprint

auth_bp = Blueprint("auth", __name__, template_folder="templates")

from . import routes  # noqa: F401, E402 — import routes to register them
