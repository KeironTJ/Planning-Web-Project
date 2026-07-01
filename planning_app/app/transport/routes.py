"""Transport department portal routes."""

from flask import render_template
from flask_login import login_required

from . import transport_bp


@transport_bp.route("/")
@transport_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("transport/dashboard.html", title="Transport")
