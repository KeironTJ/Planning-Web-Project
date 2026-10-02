"""Transport department portal routes."""

from datetime import date

from flask import abort, render_template, request
from flask_login import login_required

from app.core.decorators import permission_required
from . import transport_bp
from .services import get_loading_bay_report, get_loading_bay_state
from .load_board import get_load_board


@transport_bp.route("/")
@transport_bp.route("/dashboard")
@login_required
@permission_required("view_transport")
def dashboard():
    """Render the Transport dashboard."""
    return render_template("transport/dashboard.html", title="Transport")


@transport_bp.route("/loads")
@login_required
@permission_required("view_transport")
def loads():
    """Show current transport loads in shipping workflow order."""
    dates = {}
    for key in ("ship_from", "ship_to"):
        value = request.args.get(key, "").strip()
        try:
            dates[key] = date.fromisoformat(value) if value else None
        except ValueError:
            abort(400, description="Ship dates must use YYYY-MM-DD.")
    if dates["ship_from"] and dates["ship_to"] and dates["ship_from"] > dates["ship_to"]:
        abort(400, description="Ship from must be on or before ship to.")
    return render_template(
        "transport/loads.html", title="Transport Load Board",
        **get_load_board(
            search=request.args.get("q", ""), route=request.args.get("route", ""),
            **dates,
        ),
    )


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
