"""
API v1 — Capacity planning endpoints.

GET    /api/v1/capacity/work-centres              → list work centres
POST   /api/v1/capacity/work-centres              → create work centre
GET    /api/v1/capacity/work-centres/<id>         → get single work centre
GET    /api/v1/capacity/work-orders               → list work orders (paginated)
POST   /api/v1/capacity/work-orders               → create work order
GET    /api/v1/capacity/work-orders/<id>          → get single work order
PATCH  /api/v1/capacity/work-orders/<id>          → update work order
GET    /api/v1/capacity/utilisation               → utilisation summary
"""

from datetime import date, timedelta
from flask import request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from . import api_v1_bp
from app.auth.models import User
from app.capacity.services import WorkCentreService, CapacityService, WorkOrderService
from app.core.exceptions import NotFoundError, ValidationError, CapacityError


def _require_permission(permission_name: str):
    """Helper: return (user, None) or (None, error_response)."""
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user or not user.has_permission(permission_name):
        return None, (jsonify({"error": "Permission denied."}), 403)
    return user, None


# --- Work Centres ---

@api_v1_bp.route("/capacity/work-centres", methods=["GET"])
@jwt_required()
def list_work_centres():
    """List all active work centres."""
    user, err = _require_permission("view_capacity")
    if err:
        return err
    wcs = WorkCentreService.list_work_centres()
    return jsonify([_wc_dict(wc) for wc in wcs]), 200


@api_v1_bp.route("/capacity/work-centres", methods=["POST"])
@jwt_required()
def create_work_centre():
    user, err = _require_permission("create_capacity")
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        wc = WorkCentreService.create_work_centre(data)
        return jsonify(_wc_dict(wc)), 201
    except (ValidationError, Exception) as e:
        return jsonify({"error": str(e)}), 400


@api_v1_bp.route("/capacity/work-centres/<int:wc_id>", methods=["GET"])
@jwt_required()
def get_work_centre(wc_id: int):
    user, err = _require_permission("view_capacity")
    if err:
        return err
    try:
        wc = WorkCentreService.get_work_centre(wc_id)
        return jsonify(_wc_dict(wc)), 200
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


# --- Work Orders ---

@api_v1_bp.route("/capacity/work-orders", methods=["GET"])
@jwt_required()
def list_work_orders():
    user, err = _require_permission("view_work_orders")
    if err:
        return err
    status = request.args.get("status")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)
    pagination = WorkOrderService.list_work_orders(status=status, page=page, per_page=per_page)
    return jsonify({
        "items": [_wo_dict(wo) for wo in pagination.items],
        "total": pagination.total,
        "page": pagination.page,
        "pages": pagination.pages,
        "per_page": pagination.per_page,
    }), 200


@api_v1_bp.route("/capacity/work-orders", methods=["POST"])
@jwt_required()
def create_work_order():
    user, err = _require_permission("create_work_order")
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        wo = WorkOrderService.create_work_order(data, created_by_id=user.id)
        return jsonify(_wo_dict(wo)), 201
    except (ValidationError, Exception) as e:
        return jsonify({"error": str(e)}), 400


@api_v1_bp.route("/capacity/work-orders/<int:wo_id>", methods=["GET"])
@jwt_required()
def get_work_order(wo_id: int):
    user, err = _require_permission("view_work_orders")
    if err:
        return err
    try:
        wo = WorkOrderService.get_work_order(wo_id)
        return jsonify(_wo_dict(wo)), 200
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


@api_v1_bp.route("/capacity/work-orders/<int:wo_id>", methods=["PATCH"])
@jwt_required()
def update_work_order(wo_id: int):
    user, err = _require_permission("edit_work_order")
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        wo = WorkOrderService.update_work_order(wo_id, data)
        return jsonify(_wo_dict(wo)), 200
    except (NotFoundError, ValidationError) as e:
        return jsonify({"error": str(e)}), 400


# --- Utilisation ---

@api_v1_bp.route("/capacity/utilisation", methods=["GET"])
@jwt_required()
def utilisation_summary():
    user, err = _require_permission("view_capacity")
    if err:
        return err
    today = date.today()
    from_date = today - timedelta(days=today.weekday())
    to_date = from_date + timedelta(weeks=12)
    data = CapacityService.get_utilisation_summary(from_date, to_date)
    return jsonify(data), 200


# --- Serialisers ---

def _wc_dict(wc) -> dict:
    return {
        "id": wc.id,
        "code": wc.code,
        "name": wc.name,
        "department": wc.department,
        "hours_per_shift": float(wc.hours_per_shift),
        "shifts_per_day": wc.shifts_per_day,
        "efficiency_pct": float(wc.efficiency_pct),
        "daily_capacity_hours": float(wc.daily_capacity_hours),
        "is_active": wc.is_active,
    }


def _wo_dict(wo) -> dict:
    return {
        "id": wo.id,
        "order_number": wo.order_number,
        "product_code": wo.product_code,
        "product_description": wo.product_description,
        "quantity": float(wo.quantity),
        "quantity_completed": float(wo.quantity_completed),
        "completion_pct": round(wo.completion_pct, 1),
        "status": wo.status,
        "priority": wo.priority,
        "planned_start": wo.planned_start.isoformat() if wo.planned_start else None,
        "planned_end": wo.planned_end.isoformat() if wo.planned_end else None,
        "is_overdue": wo.is_overdue,
        "routing_id": wo.routing_id,
        "notes": wo.notes,
    }
