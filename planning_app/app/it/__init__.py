from flask import Blueprint

it_bp = Blueprint("it", __name__)

from . import routes  # noqa: F401, E402
