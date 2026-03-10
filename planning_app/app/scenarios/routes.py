"""Scenario routes - deferred to later phase."""
from . import scenarios_bp

@scenarios_bp.route("/")
def index():
    from flask import redirect, url_for
    return redirect(url_for("orders.wip_tracker"))
