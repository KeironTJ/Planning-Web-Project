"""REST API v1 blueprint."""
from flask import Blueprint
from app.extensions import csrf

api_v1_bp = Blueprint("api_v1", __name__)
csrf.exempt(api_v1_bp)

from . import auth  # noqa: F401, E402
# capacity and materials API endpoints rebuilt in Phase 7
