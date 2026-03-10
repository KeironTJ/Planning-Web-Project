"""Materials blueprint routes - Phase 6 implementation pending."""
from flask import redirect, url_for
from flask_login import login_required
from . import materials_bp

@materials_bp.route("/")
@login_required
def index():
    return redirect(url_for("orders.wip_tracker"))
