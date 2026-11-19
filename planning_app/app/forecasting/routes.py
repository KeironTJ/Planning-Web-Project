"""Browser controls for forecasting preparation.

Existing planning permissions provide read access and a separate write gate.
All writes are POST requests covered by the application's CSRF protection.
"""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError, ValidationError

from . import forecasting_bp
from .data_quality import (
    DECISION_STATUSES,
    delete_unreviewed_extract,
    extract_has_reviews,
    finding_matches,
    save_data_decision,
    save_extract_note,
    save_va05_extract,
)
from .models import ForecastDataExtract, ForecastDataNote, ForecastDataRecord
from .services import (
    get_intake_data_readiness,
    get_model_category_names,
    get_model_categories,
    get_model_review,
    remove_model_category,
    remove_model_classification,
    save_model_category,
    save_model_classification,
    save_model_classifications,
    save_model_classification_updates,
)
from .weekly_intake import CALENDAR, REQUIRED_FIELDS, RULES, explore_weekly_intake


@forecasting_bp.route("/")
@forecasting_bp.route("/dashboard")
@login_required
@permission_required("view_planning")
def dashboard():
    """Open the dedicated AI Tools section."""
    return render_template("forecasting/dashboard.html", title="AI Tools")


@forecasting_bp.route("/data-readiness")
@login_required
@permission_required("view_planning")
def data_readiness():
    """Show local order data signals and unresolved source requirements."""
    return render_template(
        "forecasting/data_readiness.html",
        title="Forecasting Data Readiness",
        readiness=get_intake_data_readiness(),
        extracts=ForecastDataExtract.query.order_by(
            ForecastDataExtract.id.desc(),
        ).paginate(page=request.args.get("page", 1, type=int), per_page=10),
        can_manage=current_user.has_permission("manage_planning"),
    )


@forecasting_bp.route("/data-readiness/capture", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_capture():
    """Save local VA05 observations and their quality results."""
    try:
        extract = save_va05_extract(current_user.id)
    except ValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("forecasting.data_readiness"))
    flash("Local VA05 extract saved. Operational data is unchanged.", "success")
    return redirect(url_for(
        "forecasting.data_extract", extract_id=extract.id,
    ))


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>")
@login_required
@permission_required("view_planning")
def data_extract(extract_id: int):
    """Inspect saved observations rather than mutable operational rows."""
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    scope = request.args.get("scope", "findings")
    if scope not in ("findings", "unreviewed", "all"):
        abort(400, description="Choose an available record filter.")
    finding = request.args.get("finding", "")
    if finding and finding not in extract.summary["rules"]:
        abort(400, description="Choose an available finding type.")
    records = ForecastDataRecord.query.filter_by(extract_id=extract.id)
    release = None
    release_anchor = request.args.get("release", type=int)
    if "release" in request.args:
        if release_anchor is None or release_anchor < 1:
            abort(400, description="Choose a valid saved release.")
        anchor = records.filter_by(id=release_anchor).first_or_404()
        release = {
            key: anchor.data[key]
            for key in ("order_num", "order_line", "rel_num")
        }
        records = records.filter(*(
            ForecastDataRecord.data[key].as_integer() == value
            for key, value in release.items()
        ))
    if scope != "all":
        records = records.filter_by(has_findings=True)
    if scope == "unreviewed":
        records = records.filter(~ForecastDataRecord.decisions.any())
    if finding:
        records = records.filter(finding_matches(finding))
    pagination = records.order_by(ForecastDataRecord.id).paginate(
        page=request.args.get("page", 1, type=int), per_page=25,
        error_out=False,
    )
    if pagination.page > max(pagination.pages, 1):
        return redirect(url_for(
            "forecasting.data_extract", extract_id=extract.id,
            scope=scope, finding=finding, page=max(pagination.pages, 1),
            release=release_anchor,
        ))
    models = {
        (record.data["model"] or "").strip()
        for record in pagination.items
        if "model" in record.data
    }
    notes = ForecastDataNote.query.filter_by(extract_id=extract.id).order_by(
        ForecastDataNote.id.desc(),
    ).all()
    can_manage = current_user.has_permission("manage_planning")
    return render_template(
        "forecasting/data_extract.html",
        title=f"VA05 Data Review {extract.reference}",
        extract=extract,
        records=pagination,
        scope=scope,
        finding=finding,
        release=release,
        release_anchor=release_anchor,
        model_categories=get_model_category_names(models),
        decision_statuses=DECISION_STATUSES,
        notes=notes,
        can_manage=can_manage,
        can_delete=can_manage and not notes and not extract_has_reviews(extract.id),
    )


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/weekly-intake")
@login_required
@permission_required("view_planning")
def weekly_intake(extract_id: int):
    """Render the weekly net GBP exploration for a single saved extract."""
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    profile = None
    if REQUIRED_FIELDS.issubset(extract.source_settings.get("fields", ())):
        records = ForecastDataRecord.query.with_entities(ForecastDataRecord.data)
        records = records.filter_by(extract_id=extract.id).yield_per(500)
        profile = explore_weekly_intake(
            (data for (data,) in records), extract.captured_at.date(),
        )
    chart_scale = None
    if profile is not None:
        magnitudes = [
            abs(week.net_gbp) for week in profile.weeks if week.net_gbp is not None
        ]
        if magnitudes:
            chart_scale = max(magnitudes) or 1
    return render_template(
        "forecasting/weekly_intake.html",
        title=f"Explore weekly intake {extract.reference}",
        extract=extract,
        profile=profile,
        calendar=CALENDAR,
        rules=RULES,
        chart_scale=chart_scale,
    )


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/notes", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_note(extract_id: int):
    """Append a note without editing the saved observation."""
    try:
        save_extract_note(
            extract_id, request.form.get("body", ""), current_user.id,
            request.form.get("capture_identity", ""),
        )
    except NotFoundError:
        abort(404)
    except ValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash("Extract note added.", "success")
    return redirect(url_for(
        "forecasting.data_extract", extract_id=extract_id, _anchor="extract-notes",
    ))


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/delete", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_delete(extract_id: int):
    """Delete an extract only after confirmation and evidence checks."""
    try:
        if request.form.get("confirm") != "yes":
            raise ValidationError("Confirm permanent deletion before continuing.")
        delete_unreviewed_extract(
            extract_id, current_user.id, request.form.get("capture_identity", ""),
        )
    except NotFoundError:
        abort(404)
    except ValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("forecasting.data_extract", extract_id=extract_id))
    flash("Saved extract deleted. Operational data is unchanged.", "success")
    return redirect(url_for("forecasting.data_readiness"))


@forecasting_bp.route(
    "/data-readiness/records/<int:record_id>/review", methods=["POST"],
)
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_record_review(record_id: int):
    """Append review evidence for a saved record."""
    record = ForecastDataRecord.query.get_or_404(record_id)
    extract = ForecastDataExtract.query.get_or_404(record.extract_id)
    scope = request.form.get("scope", "findings")
    if scope not in ("findings", "unreviewed", "all"):
        abort(400, description="Choose an available record filter.")
    finding = request.form.get("finding", "")
    if finding and finding not in extract.summary["rules"]:
        abort(400, description="Choose an available finding type.")
    try:
        save_data_decision(
            record.id, request.form.get("status", ""),
            request.form.get("rationale", ""), current_user.id,
            capture_identity=request.form.get("capture_identity", ""),
        )
    except (NotFoundError, ValidationError) as exc:
        flash(str(exc), "danger")
    else:
        flash("Review evidence saved. No source values were changed.", "success")
    return redirect(url_for(
        "forecasting.data_extract", extract_id=record.extract_id,
        page=request.form.get("page", 1, type=int),
        scope=scope,
        finding=finding,
        release=(
            record.id if request.form.get("release_context") == "1" else None
        ),
        _anchor=f"record-{record.id}",
    ))


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
        remove_model_classification(request.form.get("model", ""), current_user.id)
    except NotFoundError as exc:
        flash(str(exc), "danger")
    else:
        flash("Classification removed. The model now needs review.", "success")
    return redirect(url_for("forecasting.model_classifications"))
