"""Personal and shared factory project tracking."""

from flask import Blueprint

projects_bp = Blueprint("projects", __name__)

from . import routes  # noqa: E402, F401
