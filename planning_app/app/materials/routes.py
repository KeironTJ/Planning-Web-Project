"""Material availability blueprint routes."""

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required

from . import materials_bp
from .services import MaterialService
from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError, ValidationError, DuplicateError


@materials_bp.route("/")
@login_required
@permission_required("view_materials")
def dashboard():
    alerts = MaterialService.get_availability_alerts()
    materials = MaterialService.list_materials()
    return render_template(
        "materials/dashboard.html",
        title="Material Availability",
        materials=materials,
        alerts=alerts,
    )


@materials_bp.route("/check")
@login_required
@permission_required("view_materials")
def availability_check():
    """AJAX endpoint to check material availability."""
    code = request.args.get("code", "")
    qty = request.args.get("qty", 0, type=float)
    if not code:
        return jsonify({"error": "Material code required."}), 400
    from decimal import Decimal
    result = MaterialService.check_availability(code, Decimal(str(qty)))
    return jsonify(result)
