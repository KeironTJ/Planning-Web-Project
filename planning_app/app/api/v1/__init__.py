"""
REST API v1 blueprint.

All endpoints are prefixed /api/v1/.
Authentication uses JWT Bearer tokens obtained from /api/v1/auth/token.

Design decisions:
- API is versioned (/v1/) to allow non-breaking evolution.
- CSRF is exempt because API clients use JWT, not session cookies.
- Marshmallow schemas handle serialisation/deserialisation and validation.
"""

from flask import Blueprint
from app.extensions import csrf

api_v1_bp = Blueprint("api_v1", __name__)

# Exempt the entire API from CSRF (JWT provides its own auth)
csrf.exempt(api_v1_bp)

from . import auth, capacity, materials  # noqa: F401, E402
