"""Transport department portal routes."""

from datetime import date

from flask import abort, redirect, render_template, request, url_for
from flask_login import login_required

from app.core.decorators import permission_required
from . import transport_bp
from .services import get_loading_bay_report, get_loading_bay_state
from .load_board import get_load_board
from .overview import build_overview, build_management_summary
from .manifest_history import get_manifest_history


@transport_bp.route("/")
@transport_bp.route("/dashboard")
@login_required
@permission_required("view_transport")
def dashboard():
    """Combine load, shipping-value and physical loading-bay snapshots."""
    board = _filtered_load_board()
    shipping = get_loading_bay_report(today=board["today"])
    bay = get_loading_bay_state(today=board["today"])
    return render_template(
        "transport/dashboard.html", title="Transport Dashboard",
        **board, overview=build_overview(board), shipping=shipping, bay=bay,
        management=build_management_summary(shipping, bay),
    )


def _ship_date_filters() -> dict:
    dates = {}
    for key in ("ship_from", "ship_to"):
        value = request.args.get(key, "").strip()
        try:
            dates[key] = date.fromisoformat(value) if value else None
        except ValueError:
            abort(400, description="Ship dates must use YYYY-MM-DD.")
    if dates["ship_from"] and dates["ship_to"] and dates["ship_from"] > dates["ship_to"]:
        abort(400, description="Ship from must be on or before ship to.")
    return dates


def _filtered_load_board() -> dict:
    """Keep overview and operational filters identical."""
    return get_load_board(
        search=request.args.get("q", ""), route=request.args.get("route", ""), **_ship_date_filters(),
    )


@transport_bp.route("/manifest-history")
@login_required
@permission_required("view_transport")
def manifest_history():
    """Retained shipped contents, filtered by scheduled load-header dates."""
    try:
        page = int(request.args.get("page", "1"))
        if page < 1:
            raise ValueError
    except ValueError:
        abort(400, description="Page must be a positive integer.")
    return render_template(
        "transport/manifest_history.html", title="Shipped Manifest History",
        **get_manifest_history(
            search=request.args.get("q", ""), route=request.args.get("route", ""),
            page=page, **_ship_date_filters(),
        ),
    )


@transport_bp.route("/loads")
@login_required
@permission_required("view_transport")
def loads():
    """Show current transport loads in shipping workflow order."""
    return render_template(
        "transport/loads.html", title="Transport Load Board", **_filtered_load_board(),
    )


@transport_bp.route("/overview")
@login_required
@permission_required("view_transport")
def overview():
    """Keep the overview shortcut pointing at the combined dashboard."""
    return redirect(url_for("transport.dashboard", **request.args.to_dict()))


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
