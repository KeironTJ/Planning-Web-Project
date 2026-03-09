"""
API v1 — Authentication endpoints.

POST /api/v1/auth/token      → obtain access + refresh tokens
POST /api/v1/auth/refresh    → exchange refresh token for new access token
POST /api/v1/auth/revoke     → revoke current access token (logout)
GET  /api/v1/auth/me         → return current user details
"""

from flask import request, jsonify
from flask_jwt_extended import (
    create_access_token, create_refresh_token,
    jwt_required, get_jwt_identity, get_jwt,
)
from . import api_v1_bp
from app.auth.models import User
from app.auth.services import AuthService
from app.core.exceptions import AuthorisationError


@api_v1_bp.route("/auth/token", methods=["POST"])
def obtain_token():
    """
    Obtain JWT access and refresh tokens.

    Request body (JSON):
        { "email": "user@example.com", "password": "secret" }

    Response:
        { "access_token": "...", "refresh_token": "...", "user": {...} }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    email = data.get("email", "")
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    try:
        user = AuthService.login(
            email=email,
            password=password,
            ip=request.remote_addr,
        )
    except AuthorisationError as e:
        return jsonify({"error": str(e)}), 401

    identity = str(user.id)
    access_token = create_access_token(identity=identity)
    refresh_token = create_refresh_token(identity=identity)

    return jsonify({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": _user_dict(user),
    }), 200


@api_v1_bp.route("/auth/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh_token():
    """Exchange a valid refresh token for a new access token."""
    identity = get_jwt_identity()
    access_token = create_access_token(identity=identity)
    return jsonify({"access_token": access_token}), 200


@api_v1_bp.route("/auth/me", methods=["GET"])
@jwt_required()
def current_user_info():
    """Return authenticated user details."""
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found."}), 404
    return jsonify(_user_dict(user)), 200


def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "roles": [r.name for r in user.roles],
        "permissions": list({p.name for r in user.roles for p in r.permissions}),
        "is_admin": user.is_admin,
    }
