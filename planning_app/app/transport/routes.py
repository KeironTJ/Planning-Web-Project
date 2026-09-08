"""Transport department portal routes."""

from flask import render_template, request
from flask_login import login_required

from app.core.decorators import permission_required
from . import transport_bp
from .services import get_loading_bay_report, get_loading_bay_state


@transport_bp.route("/")
@transport_bp.route("/dashboard")
@login_required
@permission_required("view_transport")
def dashboard():
    """Render the Transport dashboard."""
    return render_template("transport/dashboard.html", title="Transport")


@transport_bp.route("/loading-bay")
@login_required
@permission_required("view_transport")
def loading_bay():
    """Render the finished-goods loading-bay report."""
    report = get_loading_bay_report(
        search=request.args.get("q", ""),
        customer=request.args.get("customer", ""),
        status=request.args.get("status", ""),
        sort=request.args.get("sort", "due_date"),
    )
    return render_template("transport/loading_bay.html", title="Loading Bay Report", **report)


@transport_bp.route("/bay-state")
@login_required
@permission_required("view_transport")
def bay_state():
    """Render the physical loading-bay state."""
    return render_template(
        "transport/bay_state.html",
        title="Loading Bay State",
        **get_loading_bay_state(),
    )
