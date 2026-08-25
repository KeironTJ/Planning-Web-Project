"""Works-order capacity planning sub-module."""

from flask import Blueprint

workorder_plan_bp = Blueprint(
    "workorder_plan",
    __name__,
    template_folder=None,
)

from . import routes  # noqa: E402,F401
