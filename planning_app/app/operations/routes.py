"""Operations department portal routes."""

from flask import render_template
from flask_login import login_required

from . import operations_bp


@operations_bp.route("/")
@operations_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("operations/dashboard.html", title="Operations")
