"""
API v1 — Material availability endpoints.

GET  /api/v1/materials                    → list materials
GET  /api/v1/materials/<id>               → get single material
GET  /api/v1/materials/check?code=X&qty=N → availability check
"""

from flask import request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from decimal import Decimal

from . import api_v1_bp
from app.auth.models import User
from app.materials.services import MaterialService
from app.core.exceptions import NotFoundError


def _require_permission(permission_name: str):
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user or not user.has_permission(permission_name):
        return None, (jsonify({"error": "Permission denied."}), 403)
    return user, None


@api_v1_bp.route("/materials", methods=["GET"])
@jwt_required()
def list_materials():
    user, err = _require_permission("view_materials")
    if err:
        return err
    below_reorder = request.args.get("below_reorder", "false").lower() == "true"
    materials = MaterialService.list_materials(below_reorder=below_reorder)
    return jsonify([_material_dict(m) for m in materials]), 200


@api_v1_bp.route("/materials/<int:material_id>", methods=["GET"])
@jwt_required()
def get_material(material_id: int):
    user, err = _require_permission("view_materials")
    if err:
        return err
    try:
        m = MaterialService.get_material(material_id)
        return jsonify(_material_dict(m)), 200
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


@api_v1_bp.route("/materials/check", methods=["GET"])
@jwt_required()
def check_availability():
    user, err = _require_permission("view_materials")
    if err:
        return err
    code = request.args.get("code", "")
    qty = request.args.get("qty", "0")
    try:
        result = MaterialService.check_availability(code, Decimal(qty))
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


def _material_dict(m) -> dict:
    return {
        "id": m.id,
        "code": m.code,
        "description": m.description,
        "unit_of_measure": m.unit_of_measure,
        "stock_on_hand": float(m.stock_on_hand),
        "safety_stock": float(m.safety_stock),
        "reorder_point": float(m.reorder_point),
        "lead_time_days": m.lead_time_days,
        "is_below_safety_stock": m.is_below_safety_stock,
        "is_below_reorder_point": m.is_below_reorder_point,
    }
