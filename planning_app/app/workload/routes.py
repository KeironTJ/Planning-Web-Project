"""Workload routes - deferred to later phase."""
from . import workload_bp

@workload_bp.route("/")
def index():
    from flask import redirect, url_for
    return redirect(url_for("orders.wip_tracker"))
