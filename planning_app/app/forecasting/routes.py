"""Browser controls for forecasting preparation.

Existing planning permissions provide read access and a separate write gate.
All writes are POST requests covered by the application's CSRF protection.
"""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError, ValidationError

from . import forecasting_bp
from .services import (
    get_model_categories,
    get_model_review,
    remove_model_category,
    remove_model_classification,
    save_model_category,
    save_model_classification,
    save_model_classifications,
    save_model_classification_updates,
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
    categories = get_model_categories()
    counts = {category.id: 0 for category in categories}
    model_counts = {category.id: 0 for category in categories}
    counts["unclassified"] = 0
    for item in items:
        if item.category_id is None:
            counts["unclassified"] += item.record_count
        else:
            counts[item.category_id] += item.record_count
            model_counts[item.category_id] += 1
    return render_template(
        "forecasting/models.html",
        title="Forecasting Model Classification",
        items=items,
        categories=categories,
        counts=counts,
        model_counts=model_counts,
        quantity_exceptions=sum(
            item.missing_qty_count + item.non_single_qty_count for item in items
        ),
        can_manage=current_user.has_permission("manage_planning"),
    )


@forecasting_bp.route("/models/categories/save", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def model_category_save():
    """Create or edit a category without changing its forecasting meaning."""
    raw_id = request.form.get("category_id", "").strip()
    if raw_id and not raw_id.isdecimal():
        flash("Invalid category selection.", "danger")
        return redirect(url_for("forecasting.model_classifications"))
    try:
        category = save_model_category(
            name=request.form.get("name", ""),
            description=request.form.get("description", ""),
            user_id=current_user.id,
            category_id=int(raw_id) if raw_id else None,
        )
    except (NotFoundError, ValidationError) as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Category '{category.name}' saved.", "success")
    return redirect(url_for("forecasting.model_classifications"))


@forecasting_bp.route("/models/categories/<int:category_id>/remove", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def model_category_remove(category_id: int):
    """Remove an unused category."""
    try:
        remove_model_category(category_id, current_user.id)
    except (NotFoundError, ValidationError) as exc:
        flash(str(exc), "danger")
    else:
        flash("Category removed.", "success")
    return redirect(url_for("forecasting.model_classifications"))


@forecasting_bp.route("/models/save", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def model_classification_save():
    """Save an exact model decision and notify the user of validation errors."""
    try:
        save_model_classification(
            request.form.get("model", ""),
            request.form.get("category_id", type=int),
            current_user.id,
        )
    except ValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash("Model classification saved.", "success")
    return redirect(url_for("forecasting.model_classifications"))


@forecasting_bp.route("/models/bulk-save", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def model_classifications_bulk_save():
    """Classify multiple selected model values in one audited action."""
    try:
        updated = save_model_classifications(
            request.form.getlist("models"),
            request.form.get("category_id", type=int),
            current_user.id,
        )
    except ValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Classification updated for {updated} model(s).", "success")
    return redirect(url_for("forecasting.model_classifications"))


@forecasting_bp.route("/models/save-all", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def model_classifications_save_all():
    """Save changed model dropdowns together in one transaction."""
    models = request.form.getlist("models")
    category_values = request.form.getlist("category_ids")
    try:
        if len(models) != len(category_values):
            raise ValidationError("Model and category selections do not match.")
        updates = []
        for model, category_value in zip(models, category_values):
            try:
                category_id = int(category_value) if category_value else None
            except ValueError as exc:
                raise ValidationError("Choose an available category.") from exc
            updates.append((model, category_id))
        updated = save_model_classification_updates(updates, current_user.id)
    except ValidationError as exc:
        flash(str(exc), "danger")
    else:
        if updated:
            flash(f"Classification updated for {updated} model(s).", "success")
        else:
            flash("No classification changes to save.", "info")
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
