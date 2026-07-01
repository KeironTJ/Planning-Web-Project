"""IT department portal routes."""

from flask import render_template
from flask_login import login_required

from . import it_bp


@it_bp.route("/")
@it_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("it/dashboard.html", title="IT")
