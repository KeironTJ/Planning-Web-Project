"""Browser controls for forecasting preparation.

Existing planning permissions provide read access and a separate write gate.
All writes are POST requests covered by the application's CSRF protection.
"""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError, ValidationError

from . import forecasting_bp
from .models import MODEL_CATEGORIES
from .services import (
    get_model_review,
    remove_model_classification,
    save_model_classification,
)


@forecasting_bp.route("/")
@forecasting_bp.route("/dashboard")
@login_required
@permission_required("view_planning")
def dashboard():
    """Open the dedicated AI Tools section."""
    return render_template("forecasting/dashboard.html", title="AI Tools")


@forecasting_bp.route("/models")
@login_required
@permission_required("view_planning")
def model_classifications():
    """Display exact model decisions and source-quantity exceptions."""
    items = get_model_review()
    counts = {category: 0 for category in (*MODEL_CATEGORIES, "unclassified")}
    for item in items:
        counts[item.category] += item.record_count
    return render_template(
        "forecasting/models.html",
        title="Forecasting Model Classification",
        items=items,
        categories=MODEL_CATEGORIES,
        counts=counts,
        quantity_exceptions=sum(
            item.missing_qty_count + item.non_single_qty_count for item in items
        ),
        can_manage=current_user.has_permission("manage_planning"),
    )


@forecasting_bp.route("/models/save", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def model_classification_save():
    """Save an exact model decision and notify the user of validation errors."""
    try:
        save_model_classification(
            request.form.get("model", ""),
            request.form.get("category", ""),
            current_user.id,
        )
    except ValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash("Model classification saved.", "success")
    return redirect(url_for("forecasting.model_classifications"))


@forecasting_bp.route("/models/remove", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def model_classification_remove():
    """Return a saved model to the unclassified review list."""
    try:
        remove_model_classification(
            request.form.get("model", ""), current_user.id
        )
    except NotFoundError as exc:
        flash(str(exc), "danger")
    else:
        flash("Classification removed. The model now needs review.", "success")
    return redirect(url_for("forecasting.model_classifications"))
