"""Orders blueprint routes — placeholder for Phase 4 implementation."""

from flask import render_template_string
from flask_login import login_required

from . import orders_bp

_PLACEHOLDER = """
{% extends 'base.html' %}
{% block content %}
<div class="container-fluid py-4">
  <h2>{{ title }}</h2>
  <div class="alert alert-info">This view is being built — check back soon.</div>
</div>
{% endblock %}
"""


@orders_bp.route("/")
@orders_bp.route("/wip")
@login_required
def wip_tracker():
    """WIP Tracker — Phase 4."""
    return render_template_string(_PLACEHOLDER, title="WIP Tracker")
