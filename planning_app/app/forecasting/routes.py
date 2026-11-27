"""Browser controls for forecasting preparation.

Existing planning permissions provide read access and a separate write gate.
All writes are POST requests covered by the application's CSRF protection.
"""

import csv
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO

from flask import Response, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, true, tuple_
from sqlalchemy.orm import lazyload

from app.core.decorators import permission_required
from app.core.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.sales.orders.models import SalesOrder

from . import forecasting_bp
from .adjustments import (
    adjustment_suggestions, confirm_row_exclusion_suggestions, create_adjusted_extract,
    finding_adjustment_counts, latest_adjustments, latest_row_exclusions,
    row_exclusion_suggestion_ids, row_exclusion_suggestions, save_adjustment, save_row_exclusions,
)
from .data_quality import (
    DECISION_STATUSES,
    FEATURE_AVAILABILITY,
    FEATURE_DECISIONS,
    MAX_PAGE_REVIEWS,
    NONPOSITIVE_GBP_GROUPS,
    confirm_review_suggestions,
    delete_unreviewed_extract,
    extract_has_reviews,
    finding_elements,
    finding_matches,
    group_nonpositive_gbp,
    matching_record_ids,
    preview_finding_reviews,
    review_suggestions,
    save_data_decision,
    save_data_decisions,
    save_finding_reviews,
    save_extract_note,
    save_feature_decision,
    save_va05_extract,
)
from .models import (
    ForecastDataDecision, ForecastDataExtract, ForecastDataNote, ForecastDataRecord,
    ForecastExperiment, ForecastFeatureDecision, ForecastReleaseAdjustment, ForecastRowExclusion,
)
from .experiments import dependencies_available, launch_experiment, start_experiment
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
from .weekly_intake import (
    CALENDAR, END, FIRST_WEEK, MIX_AXES, REQUIRED_FIELDS, RULES, WEEK,
    IntakeWeek, MixSummary, available_mix_axes, explore_intake_history, explore_weekly_intake,
    customer_concentration, financial_month, financial_year, financial_year_start,
    score_forecasts, seasonal_heatmap, summarize_mix, summarize_time_patterns,
    summarize_weekly_distribution,
)


WORKFLOW_TODO = {
    "distribution": (
        "Distribution and unusual weeks",
        "Separate genuine intake spikes from reporting or coverage exceptions before deciding how to treat them.",
        ("Plot the spread of completed weekly totals", "Flag unusual weeks and show what contributed",
         "Compare results with and without flagged weeks without changing the saved extract"),
    ),
    "time-patterns": (
        "Time patterns and lags",
        "Decide which historical inputs are worth testing and which calendar patterns lack evidence.",
        ("Plot changes across financial weeks", "Check lag relationships without bridging gaps",
         "Review shutdowns and assess whether enough verified history exists for seasonality"),
    ),
    "feature-decisions": (
        "Feature decisions",
        "Connect EDA findings to what the first model will use, defer or reject.",
        ("Link evidence for each candidate input", "Check what is known at forecast issue time",
         "Record the decision and its rationale before fitting"),
    ),
    "experiments": (
        "Model experiments",
        "Compare learned forecasts with simple benchmarks on the same target weeks.",
        ("Set rolling development cutoffs", "Compare previous-week, four-week-average and learned forecasts",
         "Report WAPE, GBP error and bias, then check performance prospectively"),
    ),
    "forecasts": (
        "Forecasts",
        "Let planners choose from evaluated models and see the evidence behind each choice.",
        ("Show model and data versions", "Visualise predictions and uncertainty",
         "Save issued forecasts for later comparison"),
    ),
    "monitoring": (
        "Monitoring",
        "Track whether issued forecasts remain useful as actuals arrive.",
        ("Compare issued forecasts with actual intake", "Track error and bias over time",
         "Agree when to review, retrain or replace a model"),
    ),
}


def _week_in_extract(extract: ForecastDataExtract, number: int | None, year: int | None) -> bool:
    if year is None or not 1 <= year <= extract.captured_at.year or number is None:
        return False
    first = financial_year_start(year)
    if not 1 <= number <= (financial_year_start(year + 1) - first) // WEEK:
        return False
    start = first + (number - 1) * WEEK
    end = start + WEEK
    period = extract.order_period
    return start <= extract.captured_at.date() and (
        period is None or (start <= period[1] and end > period[0])
    )


def _week_year(values) -> int | None:
    return values.get("year", type=int) if "year" in values else 2026


def _intake_years(weeks):
    years = {}
    for week in weeks:
        years.setdefault(financial_year(week.start), []).append(week)
    return years


def _intake_records(extract_id: int, names: dict[str, set[str]] | None = None):
    records = ForecastDataRecord.query.with_entities(ForecastDataRecord.data)
    for (data,) in records.filter_by(extract_id=extract_id).yield_per(500):
        if names is not None:
            customer_id, name = data.get("customer_id"), data.get("customer_name")
            if isinstance(customer_id, str) and customer_id.strip() and isinstance(name, str) and name.strip():
                names.setdefault(customer_id.strip(), set()).add(name.strip())
        yield data


def _customer_names(
    summary: MixSummary | None, captured: bool, names: dict[str, set[str]],
) -> dict[str, str]:
    if summary is None:
        return {}
    ids = {label[0] for label, _, _ in summary.breakdown}
    if not captured and ids:
        rows = db.session.query(SalesOrder.customer_id, SalesOrder.customer_name).filter(
            SalesOrder.customer_id.isnot(None), SalesOrder.customer_name.isnot(None),
        ).distinct().yield_per(500)
        for identity, name in rows:
            identity, name = identity.strip(), name.strip()
            if identity in ids and name:
                names.setdefault(identity, set()).add(name)
    return {
        identity: (
            next(iter(candidates)) if len(candidates) == 1
            else "Conflicting names" if candidates else "Name unavailable"
        )
        for identity in ids for candidates in (names.get(identity, set()),)
    }


@forecasting_bp.route("/")
@forecasting_bp.route("/dashboard")
@login_required
@permission_required("view_planning")
def dashboard():
    """Open the dedicated AI Tools section."""
    extract_id = request.args.get("extract_id", type=int)
    extract = (
        ForecastDataExtract.query.get_or_404(extract_id) if extract_id is not None
        else ForecastDataExtract.query.order_by(ForecastDataExtract.id.desc()).first()
    )
    counts = _extract_review_counts(extract.id) if extract else {}
    unreviewed = extract.summary.get("flagged_rows", 0) - sum(counts.values()) if extract else 0
    return render_template(
        "forecasting/dashboard.html", title="AI Tools", extract=extract,
        review_counts=counts, unreviewed=unreviewed,
    )


@forecasting_bp.route("/eda")
@forecasting_bp.route("/eda/<int:extract_id>")
@login_required
@permission_required("view_planning")
def eda(extract_id: int | None = None):
    """Choose the saved extract for the browser EDA workflow."""
    extract = (
        ForecastDataExtract.query.get_or_404(extract_id) if extract_id is not None
        else ForecastDataExtract.query.order_by(ForecastDataExtract.id.desc()).first()
    )
    fields = extract.source_settings.get("fields", ()) if extract is not None else ()
    return render_template(
        "forecasting/eda.html", title="Explore intake",
        extract=extract, has_weekly=REQUIRED_FIELDS.issubset(fields),
        captured_mix=available_mix_axes(fields), mix_axes=MIX_AXES,
    )


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/history")
@login_required
@permission_required("view_planning")
def intake_history(extract_id: int):
    """Review financial coverage of the immutable saved extract."""
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    if not REQUIRED_FIELDS.issubset(extract.source_settings.get("fields", ())):
        abort(400, description="This saved extract lacks required weekly GBP fields.")
    records = ForecastDataRecord.query.with_entities(ForecastDataRecord.data)
    records = records.filter_by(extract_id=extract.id).yield_per(500)
    captured_on = extract.captured_at.date()
    profile = explore_intake_history(
        (data for (data,) in records), captured_on, period=extract.order_period,
    )
    years: dict[int, dict[int, list[IntakeWeek]]] = {}
    for week in profile.weeks:
        years.setdefault(financial_year(week.start), {}).setdefault(
            financial_month(week.number), [],
        ).append(week)

    return render_template(
        "forecasting/intake_history.html", title=f"Coverage review {extract.reference}",
        extract=extract, years=years, weeks=profile.weeks,
        unassigned_dates=profile.issues["Releases with missing or conflicting order dates"],
        future_dates=profile.issues["Releases dated after the capture"],
    )


@forecasting_bp.route("/workflow/<stage>")
@login_required
@permission_required("view_planning")
def workflow_todo(stage: str):
    """Show planned work without implying the analysis or models already exist."""
    if stage not in WORKFLOW_TODO:
        abort(404)
    if stage == "experiments":
        return _experiment_page()
    if stage == "time-patterns" and request.args.get("extract_id", type=int) is not None:
        return redirect(url_for("forecasting.time_patterns", extract_id=request.args.get("extract_id", type=int)))
    if stage == "feature-decisions" and request.args.get("extract_id", type=int) is not None:
        return redirect(url_for("forecasting.feature_decisions", extract_id=request.args.get("extract_id", type=int)))
    title, purpose, tasks = WORKFLOW_TODO[stage]
    extract_id = request.args.get("extract_id", type=int)
    return render_template(
        "forecasting/workflow_todo.html", title=title, purpose=purpose, tasks=tasks,
        eda_stage=stage in {"distribution", "time-patterns", "feature-decisions"},
        extract=ForecastDataExtract.query.get_or_404(extract_id) if extract_id is not None else None,
        extract_id=extract_id,
    )


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/features")
@login_required
@permission_required("view_planning")
def feature_decisions(extract_id: int):
    """Review append-only feature assessments for one saved extract."""
    return _feature_decisions_page(extract_id)


def _feature_decisions_page(extract_id: int, draft=None):
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    entries = ForecastFeatureDecision.query.filter_by(extract_id=extract.id).order_by(
        ForecastFeatureDecision.id.desc(),
    ).all()
    features = {}
    for entry in entries:
        features.setdefault(entry.feature_key, []).append(entry)
    return render_template(
        "forecasting/feature_decisions.html", title="Feature decisions",
        extract=extract, features=features, decisions=FEATURE_DECISIONS,
        availability=FEATURE_AVAILABILITY,
        can_manage=current_user.has_permission("manage_planning"),
        has_weekly=REQUIRED_FIELDS.issubset(extract.source_settings.get("fields", ())),
        draft=draft,
    )


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/features", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def feature_decision_save(extract_id: int):
    try:
        save_feature_decision(
            extract_id, current_user.id, request.form.get("capture_identity", ""),
            request.form.get("expected_id", type=int),
            feature=request.form.get("feature", ""),
            decision=request.form.get("decision", ""),
            availability=request.form.get("availability", ""),
            coverage=request.form.get("coverage", ""),
            evidence=request.form.get("evidence", ""),
            cutoff_details=request.form.get("cutoff_details", ""),
            rationale=request.form.get("rationale", ""),
        )
    except NotFoundError:
        abort(404)
    except ValidationError as exc:
        flash(str(exc), "danger")
        return _feature_decisions_page(extract_id, request.form), 400
    else:
        flash("Feature decision recorded.", "success")
    return redirect(url_for("forecasting.feature_decisions", extract_id=extract_id))


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/time-patterns")
@login_required
@permission_required("view_planning")
def time_patterns(extract_id: int):
    """Explore the saved weekly history without changing it."""
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    if not REQUIRED_FIELDS.issubset(extract.source_settings.get("fields", ())):
        abort(400, description="This saved extract lacks required weekly GBP fields.")
    records = ForecastDataRecord.query.filter_by(extract_id=extract.id).with_entities(
        ForecastDataRecord.data,
    ).yield_per(500)
    profile = explore_intake_history(
        (data for (data,) in records), extract.captured_at.date(), period=extract.order_period,
    )
    lags, sensitivity, comparisons = summarize_time_patterns(profile.weeks)
    years = {}
    for week in profile.weeks:
        years.setdefault(financial_year(week.start), []).append(week)

    chart_years = {}
    values = [week.net_gbp for week in profile.weeks if not week.partial and week.net_gbp is not None]
    if values:
        low, high = min(values), max(values)
        span = high - low
        for year, weeks in years.items():
            points = []
            for week in weeks:
                if week.partial or week.net_gbp is None:
                    continue
                relative = (week.net_gbp - low) / span if span else Decimal("0.5")
                points.append({
                    "week": week,
                    "x": round(70 + (week.number - 1) * 10, 1),
                    "y": round(160 - float(relative) * 120, 1),
                })
            chart_years[year] = points
    else:
        low = high = None

    previous_pairs = []
    seasonal_pairs = []
    for row in comparisons:
        assert row.target.net_gbp is not None
        assert row.previous_week.net_gbp is not None
        assert row.previous_year.net_gbp is not None
        previous_pairs.append((row.previous_week.net_gbp, row.target.net_gbp))
        seasonal_pairs.append((row.previous_year.net_gbp, row.target.net_gbp))
    scores = {
        "Previous week": score_forecasts(previous_pairs),
        "Same financial week last year": score_forecasts(seasonal_pairs),
    }
    return render_template(
        "forecasting/time_patterns.html", title="Time patterns and lags",
        extract=extract, years=years, chart_years=chart_years, low=low, high=high,
        heatmap=seasonal_heatmap(years),
        weeks_by_number={year: {week.number: week for week in weeks} for year, weeks in years.items()},
        lags=lags, sensitivity=sensitivity,
        comparisons=comparisons, scores=scores, rules=RULES,
    )


_EXPERIMENT_MODELS = {
    "previous_week": "Previous week",
    "four_week_mean": "Four-week average",
    "regression": "Linear regression",
    "arima": "ARIMA",
    "gradient_boosting": "Gradient boosting",
}


def _experiment_models(run):
    models = dict(_EXPERIMENT_MODELS)
    if "boosting_week" in run.result["settings"]:
        models["gradient_boosting_week"] = "Gradient boosting + financial week"
    return models


def _experiment_page():
    from scripts.weekly_intake_review import score_forecasts

    extract_id = request.args.get("extract_id", type=int)
    extract = (
        ForecastDataExtract.query.get_or_404(extract_id) if extract_id is not None
        else ForecastDataExtract.query.order_by(ForecastDataExtract.id.desc()).first()
    )
    runs = (
        ForecastExperiment.query.filter_by(extract_id=extract.id).order_by(ForecastExperiment.id.desc()).all()
        if extract else []
    )
    run_id = request.args.get("run_id", type=int)
    if "run_id" in request.args and (run_id is None or run_id < 1):
        abort(400, description="Choose a valid experiment.")
    run = next((item for item in runs if item.id == run_id), None) if run_id else (runs[0] if runs else None)
    if run_id and run is None:
        abort(404)
    scenario = request.args.get("scenario", default=0, type=int)
    model = request.args.get("model", "arima")
    year = request.args.get("year", type=int)
    models = _experiment_models(run) if run and run.status == "completed" else dict(_EXPERIMENT_MODELS)
    if scenario is None or not 0 <= scenario < 4 or model not in models:
        abort(400, description="Choose a valid model and sensitivity.")
    rows = []
    scores = {}
    years = []
    chart = None
    largest = []
    if run and run.status == "completed":
        results = run.result
        all_rows = results["scenarios"][scenario]["forecasts"]
        years = sorted({financial_year(date.fromisoformat(row["week_start"])) for row in all_rows})
        if request.args.get("year") and year not in years:
            abort(400, description="Choose an available financial year.")
        rows = [dict(row) for row in all_rows if year is None or financial_year(date.fromisoformat(row["week_start"])) == year]
        for key in models:
            scores[key] = score_forecasts([
                (Decimal(row[key]), Decimal(row["actual"])) for row in rows
            ])
        for row in rows:
            row["error"] = Decimal(row[model]) - Decimal(row["actual"])
            row["year"] = financial_year(date.fromisoformat(row["week_start"]))
        largest = sorted(rows, key=lambda row: abs(row["error"]), reverse=True)[:10]
        if rows:
            first = date.fromisoformat(rows[0]["week_start"])
            last = date.fromisoformat(rows[-1]["week_start"])
            values = [Decimal(row[key]) for row in rows for key in ("actual", model)]
            low, high = min(values), max(values)
            span = high - low or Decimal(1)
            days = max((last - first).days, 1)
            segments = {"actual": [], "predicted": []}
            current_segments = {"actual": [], "predicted": []}
            dots = {"actual": [], "predicted": []}
            previous = None
            for row in rows:
                day = date.fromisoformat(row["week_start"])
                if previous is not None and (day - previous).days != 7:
                    for key in segments:
                        segments[key].append(" ".join(current_segments[key]))
                        current_segments[key] = []
                x = 70 + 875 * (day - first).days / days
                for key, value in (("actual", row["actual"]), ("predicted", row[model])):
                    y = 205 - float((Decimal(value) - low) / span) * 170
                    current_segments[key].append(f"{x:.2f},{y:.2f}")
                    dots[key].append((round(x, 2), round(y, 2)))
                previous = day
            for key in segments:
                segments[key].append(" ".join(current_segments[key]))
            chart = {"segments": segments, "dots": dots, "first": first, "last": last, "low": low, "high": high}
    return render_template(
        "forecasting/experiments.html", title="Compare models", extract=extract, runs=runs, run=run,
        models=models, selected_model=model, scenario=scenario, selected_year=year,
        years=years, rows=rows, scores=scores, chart=chart, largest=largest,
        can_manage=current_user.has_permission("manage_planning"),
        can_run=current_app.config.get("APP_ENVIRONMENT") != "production",
        dependencies=dependencies_available(),
    )


@forecasting_bp.get("/experiments/history")
@login_required
@permission_required("view_planning")
def experiment_history():
    """Compare saved development runs on their common, unchanged target actuals."""
    entries = (
        db.session.query(ForecastExperiment, ForecastDataExtract)
        .join(ForecastDataExtract, ForecastExperiment.extract_id == ForecastDataExtract.id)
        .filter(ForecastExperiment.status == "completed")
        .order_by(ForecastExperiment.id.desc()).all()
    )
    by_id = {run.id: (run, extract) for run, extract in entries}
    selections = []
    for name, default in (("baseline", entries[1] if len(entries) > 1 else (entries[0] if entries else None)),
                          ("candidate", entries[0] if entries else None)):
        if name in request.args:
            selected_id = request.args.get(name, type=int)
            if selected_id not in by_id:
                abort(400, description="Choose a completed saved run.")
            selections.append(by_id[selected_id])
        else:
            selections.append(default)
    baseline, candidate = selections
    available_scenarios = []
    comparison = None
    selected_scenario = request.args.get("scenario")
    baseline_model = request.args.get("baseline_model", "gradient_boosting")
    candidate_model = request.args.get("candidate_model", "gradient_boosting")
    if baseline and candidate:
        left, right = baseline[0], candidate[0]
        all_models = {**_EXPERIMENT_MODELS, "gradient_boosting_week": "Gradient boosting + financial week"}
        left_models, right_models = _experiment_models(left), _experiment_models(right)
        if baseline_model not in all_models or candidate_model not in all_models:
            abort(400, description="Choose a model available in each saved run.")
        left_scenarios = {item["label"]: item for item in left.result["scenarios"]}
        right_scenarios = {item["label"]: item for item in right.result["scenarios"]}
        available_scenarios = [label for label in left_scenarios if label in right_scenarios]
        if selected_scenario is None:
            selected_scenario = (
                "Without quality-flagged weeks"
                if "Without quality-flagged weeks" in available_scenarios
                else (available_scenarios[0] if available_scenarios else None)
            )
        if not available_scenarios:
            abort(400, description="These runs have no sensitivity in common.")
        if selected_scenario not in available_scenarios:
            abort(400, description="Choose a sensitivity available in both saved runs.")
        left_rows = left_scenarios[selected_scenario]["forecasts"]
        right_rows = right_scenarios[selected_scenario]["forecasts"]
        left_by_week = {row["week_start"]: row for row in left_rows}
        right_by_week = {row["week_start"]: row for row in right_rows}
        if len(left_by_week) != len(left_rows) or len(right_by_week) != len(right_rows):
            raise ValueError("A saved experiment contains duplicate target weeks.")
        shared = sorted(left_by_week.keys() & right_by_week.keys())
        common = [
            week for week in shared
            if Decimal(left_by_week[week]["actual"]) == Decimal(right_by_week[week]["actual"])
        ]
        same_rules = left.result["analysis_rules"] == right.result["analysis_rules"]
        def score(rows, model, models):
            if model not in models:
                return None
            return score_forecasts([(Decimal(row[model]), Decimal(row["actual"])) for row in rows])
        comparison = {
            "baseline_all": score(left_rows, baseline_model, left_models),
            "candidate_all": score(right_rows, candidate_model, right_models),
            "baseline_common": score([left_by_week[week] for week in common], baseline_model, left_models) if same_rules else None,
            "candidate_common": score([right_by_week[week] for week in common], candidate_model, right_models) if same_rules else None,
            "shared_count": len(shared),
            "changed_actuals": len(shared) - len(common),
            "baseline_only": len(left_by_week.keys() - right_by_week.keys()),
            "candidate_only": len(right_by_week.keys() - left_by_week.keys()),
            "same_rules": same_rules,
            "same_extract": left.extract_hash == right.extract_hash,
            "left_models": left_models,
            "right_models": right_models,
            "all_models": all_models,
        }
    return render_template(
        "forecasting/experiment_history.html", title="Performance history",
        entries=entries, baseline=baseline, candidate=candidate, comparison=comparison,
        scenarios=available_scenarios, selected_scenario=selected_scenario,
        baseline_model=baseline_model, candidate_model=candidate_model,
    )


@forecasting_bp.post("/experiments/<int:extract_id>/start")
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def experiment_start(extract_id: int):
    if current_app.config.get("APP_ENVIRONMENT") == "production":
        abort(403, description="Development model experiments are not enabled in production.")
    if not dependencies_available():
        flash("Install the optional forecasting dependencies before starting an experiment.", "warning")
        return redirect(url_for("forecasting.workflow_todo", stage="experiments", extract_id=extract_id))
    try:
        run = start_experiment(extract_id, request.form.get("capture_identity", ""), current_user.id)
    except (ValidationError, NotFoundError) as exc:
        flash(str(exc), "warning")
        return redirect(url_for("forecasting.workflow_todo", stage="experiments", extract_id=extract_id))
    try:
        launch_experiment(current_app._get_current_object(), run.id)
    except RuntimeError:
        current_app.logger.exception("Could not start forecast experiment %s", run.id)
        run.status = "failed"
        run.active_key = None
        run.error = "Could not start the background worker. Check application logs."
        run.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        flash(run.error, "danger")
    return redirect(url_for("forecasting.workflow_todo", stage="experiments", extract_id=extract_id, run_id=run.id))


@forecasting_bp.route("/data-readiness")
@login_required
@permission_required("view_planning")
def data_readiness():
    """Show local order data signals and unresolved source requirements."""
    copy_from = request.args.get("copy_from", type=int)
    copy_period = ForecastDataExtract.query.get_or_404(copy_from).order_period if copy_from else None
    return render_template(
        "forecasting/data_readiness.html",
        title="Prepare data",
        readiness=get_intake_data_readiness(),
        calendar=CALENDAR,
        calendar_start=FIRST_WEEK,
        calendar_end=END - timedelta(days=1),
        extracts=ForecastDataExtract.query.order_by(
            ForecastDataExtract.id.desc(),
        ).paginate(page=request.args.get("page", 1, type=int), per_page=10),
        can_manage=current_user.has_permission("manage_planning"),
        copy_period=copy_period,
    )


@forecasting_bp.route("/data-readiness/capture", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_capture():
    """Save local VA05 observations and their quality results."""
    try:
        try:
            period = (
                datetime.strptime(request.form.get("order_date_from", "").strip(), "%d/%m/%Y").date(),
                datetime.strptime(request.form.get("order_date_to", "").strip(), "%d/%m/%Y").date(),
            )
        except ValueError:
            raise ValidationError("Enter both extract dates in DD/MM/YYYY format.") from None
        extract = save_va05_extract(current_user.id, period=period)
    except ValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("forecasting.data_readiness", _anchor="saved-extracts"))
    flash("Local VA05 extract saved. Operational data is unchanged.", "success")
    return redirect(url_for(
        "forecasting.data_extract", extract_id=extract.id,
    ))


_REVIEW_BADGES = {
    "accepted": "text-bg-success",
    "unreviewed": "text-bg-secondary",
    "investigate": "text-bg-warning",
    "source_correction": "text-bg-danger",
    "no_findings": "text-bg-light",
}
_REVIEW_SCOPES = ("findings", "unreviewed", "all", "exclusion_suggestions", *DECISION_STATUSES)
_BULK_FINDING_SCOPES = ("findings", "unreviewed", "all")
_WORKLISTS = {
    "unreviewed": ("Not reviewed", "not-reviewed"),
    "investigate": (DECISION_STATUSES["investigate"], "needs-investigation"),
    "source_correction": (DECISION_STATUSES["source_correction"], "source-corrections"),
}


def _latest_extract_reviews(extract_id: int):
    latest = (
        ForecastDataDecision.query.with_entities(func.max(ForecastDataDecision.id).label("id"))
        .join(ForecastDataRecord, ForecastDataRecord.id == ForecastDataDecision.record_id)
        .filter(ForecastDataRecord.extract_id == extract_id)
        .group_by(ForecastDataDecision.record_id).subquery()
    )
    return ForecastDataDecision.query.join(latest, latest.c.id == ForecastDataDecision.id)


def _extract_review_counts(extract_id: int) -> dict[str, int]:
    return dict(_latest_extract_reviews(extract_id).with_entities(
        ForecastDataDecision.status, func.count(),
    ).group_by(ForecastDataDecision.status).all())


def _finding_review_counts(extract_id: int) -> dict[str, int]:
    elements = finding_elements()
    return dict(
        _latest_extract_reviews(extract_id).with_entities(
            elements.c.value, func.count(),
        ).join(ForecastDataRecord, ForecastDataRecord.id == ForecastDataDecision.record_id)
        .join(elements, true()).group_by(elements.c.value).all()
    )


def _gbp_review_counts(extract_id: int, groups: dict[str, list[int]]) -> dict[str, dict[str, int]]:
    counts = {"all": {}}
    latest = dict(_latest_extract_reviews(extract_id).with_entities(
        ForecastDataDecision.record_id, ForecastDataDecision.status,
    ))
    for group, ids in groups.items():
        counts[group] = {}
        for record_id in ids:
            status = latest.get(record_id, "unreviewed")
            counts[group][status] = counts[group].get(status, 0) + 1
            counts["all"][status] = counts["all"].get(status, 0) + 1
    return counts


@forecasting_bp.route(
    "/data-readiness/extracts/<int:extract_id>/source-corrections.csv",
    defaults={"status": "source_correction"},
)
@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/review-worklist/<status>.csv")
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def source_correction_worklist(extract_id: int, status: str):
    """Download flagged rows by their latest review outcome, or lack of one."""
    if status not in _WORKLISTS:
        abort(400, description="Choose an available worklist status.")
    label, filename = _WORKLISTS[status]
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    finding = request.args.get("finding", "")
    if finding and finding not in extract.summary["rules"]:
        abort(400, description="Choose an available finding type.")
    gbp_group = _gbp_review_group(finding, request.args, strict=True)
    filters = []
    if finding:
        filters.append(finding_matches(finding))
    if gbp_group is not None:
        filters.append(matching_record_ids(_nonpositive_gbp_groups(extract.id)[gbp_group]))
    if status == "unreviewed":
        records = ForecastDataRecord.query.filter_by(extract_id=extract.id, has_findings=True).filter(
            ~ForecastDataRecord.decisions.any(), *filters,
        )
        entries = ((None, record) for record in records.options(
            lazyload(ForecastDataRecord.decisions),
        ).order_by(ForecastDataRecord.id).yield_per(500))
    else:
        entries = (
            _latest_extract_reviews(extract.id)
            .add_entity(ForecastDataRecord)
            .join(ForecastDataRecord, ForecastDataRecord.id == ForecastDataDecision.record_id)
            .filter(ForecastDataDecision.status == status, *filters)
            .options(lazyload(ForecastDataRecord.decisions))
            .order_by(ForecastDataRecord.id).yield_per(500)
        )
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow((
        "Extract", "Saved record ID", "Order", "Line", "Release", "Job", "Assembly",
        "Order date (ISO)", "Findings", "Decision", "Reason", "Reviewed at (ISO)",
        "Required ship-by date (ISO)",
    ))

    def safe_cell(value):
        if isinstance(value, str) and (
            value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))
        ):
            return "'" + value
        return value

    for decision, record in entries:
        writer.writerow(map(safe_cell, (
            extract.reference, record.id,
            *(record.data.get(key) for key in ("order_num", "order_line", "rel_num", "job_num", "assembly_seq", "order_date")),
            "; ".join(extract.summary["rules"][rule] for rule in record.findings),
            label, decision.rationale if decision else "", decision.reviewed_at.isoformat() if decision else "",
            record.data.get("req_date"),
        )))
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{extract.reference}-{filename}.csv"',
            "Cache-Control": "no-store",
        },
    )


@forecasting_bp.post("/data-readiness/extracts/<int:extract_id>/finding-reviews")
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_finding_reviews(extract_id: int):
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    finding = request.form.get("finding", "")
    if not finding or finding not in extract.summary["rules"]:
        abort(400, description="Choose one saved finding type.")
    gbp_group = _gbp_review_group(finding, request.form, strict=True)
    scope = request.form.get("scope")
    if scope not in _BULK_FINDING_SCOPES:
        abort(400, description="Choose an available bulk review filter.")
    status = request.form.get("status", "")
    expected_count = request.form.get("expected_count", type=int)
    expected_revision = request.form.get("expected_revision", type=int)
    try:
        if expected_count is None or expected_count < 1 or expected_revision is None or request.form.get("confirm") != "yes":
            raise ValidationError("Confirm which rows will receive the shared outcome before saving.")
        count = save_finding_reviews(
            extract.id, finding, status, request.form.get("rationale", ""), current_user.id,
            capture_identity=request.form.get("capture_identity", ""), expected_count=expected_count,
            expected_revision=expected_revision, unreviewed_only=scope == "unreviewed",
            record_ids=_nonpositive_gbp_groups(extract.id)[gbp_group] if gbp_group is not None else None,
            remember=request.form.get("remember") == "yes",
        )
    except NotFoundError:
        abort(404)
    except ValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Saved '{DECISION_STATUSES[status]}' for {count} rows. No Epicor data was changed.", "success")
        scope = status
    return redirect(url_for(
        "forecasting.data_extract", extract_id=extract.id, finding=finding, scope=scope,
        gbp_group=gbp_group, per_page=request.form.get("per_page", type=int),
        _anchor="saved-records",
    ))


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>")
@login_required
@permission_required("view_planning")
def data_extract(extract_id: int):
    """Inspect saved observations rather than mutable operational rows."""
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    investigation_week = request.args.get("week", type=int)
    year = _week_year(request.args)
    if "week" in request.args and not _week_in_extract(extract, investigation_week, year):
        abort(400, description="Choose a week covered by the saved extract.")
    scope = request.args.get("scope", "findings")
    if scope not in _REVIEW_SCOPES:
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
    if scope == "exclusion_suggestions":
        if not current_user.has_permission("manage_planning"):
            abort(403)
        records = records.filter(matching_record_ids(
            row_exclusion_suggestion_ids(extract, latest_row_exclusions(extract.id))
        ))
    elif scope != "all":
        records = records.filter_by(has_findings=True)
    if scope == "unreviewed":
        records = records.filter(~ForecastDataRecord.decisions.any())
    elif scope in DECISION_STATUSES:
        matching = _latest_extract_reviews(extract.id).with_entities(
            ForecastDataDecision.record_id,
        ).filter(ForecastDataDecision.status == scope)
        records = records.filter(ForecastDataRecord.id.in_(matching.statement))
    if finding:
        records = records.filter(finding_matches(finding))
    gbp_group = _gbp_review_group(finding, request.args)
    gbp_groups = None
    group_ids = None
    if finding == "nonpositive_value" and not release:
        gbp_groups = _nonpositive_gbp_groups(extract.id)
        if gbp_group is not None:
            group_ids = gbp_groups[gbp_group]
            records = records.filter(matching_record_ids(group_ids))
    page_size = request.args.get("per_page", type=int) if "per_page" in request.args else 25
    if page_size not in (25, 50, MAX_PAGE_REVIEWS):
        abort(400, description="Choose an available page size.")
    page_size_param = page_size if "per_page" in request.args else None
    pagination = records.order_by(ForecastDataRecord.id).paginate(
        page=request.args.get("page", 1, type=int), per_page=page_size,
        error_out=False,
    )
    if pagination.page > max(pagination.pages, 1):
        return redirect(url_for(
            "forecasting.data_extract", extract_id=extract.id,
            scope=scope, finding=finding, page=max(pagination.pages, 1),
            release=release_anchor, gbp_group=gbp_group,
            week=investigation_week, year=request.args.get("year", type=int),
            per_page=page_size_param,
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
    adjustments = latest_adjustments(extract.id) if extract.derived_from_id is None else {}
    row_exclusions = latest_row_exclusions(extract.id) if extract.derived_from_id is None else {}
    adjustment_hints = adjustment_suggestions(extract, pagination.items, adjustments) if can_manage and extract.derived_from_id is None else {}
    adjustment_provenance = []
    adjustment_history = []
    row_exclusion_provenance = []
    row_exclusion_history = []
    if extract.derived_from_id is not None:
        ids = extract.source_settings.get("adjustment_ids", [])
        adjustment_provenance = ForecastReleaseAdjustment.query.filter(
            ForecastReleaseAdjustment.id.in_(ids),
            ForecastReleaseAdjustment.extract_id == extract.derived_from_id,
        ).order_by(ForecastReleaseAdjustment.id).all()
        if len(adjustment_provenance) != len(ids):
            abort(409, description="Adjustment provenance is incomplete; this extract requires investigation.")
        row_ids = extract.source_settings.get("row_exclusion_ids", [])
        row_exclusion_provenance = ForecastRowExclusion.query.filter(
            ForecastRowExclusion.id.in_(row_ids),
            ForecastRowExclusion.extract_id == extract.derived_from_id,
        ).order_by(ForecastRowExclusion.id).all()
        if len(row_exclusion_provenance) != len(row_ids):
            abort(409, description="Row exclusion provenance is incomplete; this extract requires investigation.")
    elif adjustments:
        adjustment_history = ForecastReleaseAdjustment.query.filter_by(extract_id=extract.id).order_by(
            ForecastReleaseAdjustment.id.desc()
        ).all()
    if row_exclusions:
        row_exclusion_history = ForecastRowExclusion.query.filter_by(extract_id=extract.id).order_by(
            ForecastRowExclusion.id.desc()
        ).all()
    bulk_preview = (
        preview_finding_reviews(
            extract.id, finding, unreviewed_only=scope == "unreviewed", record_ids=group_ids,
        )
        if can_manage and finding and not release and investigation_week is None
        and scope in _BULK_FINDING_SCOPES
        else None
    )
    return render_template(
        "forecasting/data_extract.html",
        title="Review data",
        extract=extract,
        records=pagination,
        page_size_param=page_size_param,
        page_sizes=(25, 50, MAX_PAGE_REVIEWS),
        scope=scope,
        finding=finding,
        gbp_group=gbp_group,
        gbp_group_counts={key: len(ids) for key, ids in gbp_groups.items()} if gbp_groups is not None else None,
        gbp_review_counts=_gbp_review_counts(extract.id, gbp_groups) if gbp_groups is not None else None,
        gbp_group_labels=NONPOSITIVE_GBP_GROUPS,
        release=release,
        release_anchor=release_anchor,
        investigation_week=investigation_week,
        investigation_year=request.args.get("year", type=int),
        order_period=extract.order_period,
        has_coverage=REQUIRED_FIELDS.issubset(extract.source_settings.get("fields", ())),
        model_categories=get_model_category_names(models),
        decision_statuses=DECISION_STATUSES,
        review_badges=_REVIEW_BADGES,
        notes=notes,
        can_manage=can_manage,
        review_counts=_extract_review_counts(extract.id),
        finding_review_counts=_finding_review_counts(extract.id),
        finding_adjustment_counts=finding_adjustment_counts(extract.id, adjustments, row_exclusions),
        bulk_preview=bulk_preview,
        suggestions=review_suggestions(extract, pagination.items),
        adjustments=adjustments,
        row_exclusions=row_exclusions,
        row_exclusion_history=row_exclusion_history,
        row_exclusion_provenance=row_exclusion_provenance,
        row_exclusion_revision=max((entry.id for entry in row_exclusions.values()), default=0),
        row_exclusion_hints=row_exclusion_suggestions(extract, pagination.items, row_exclusions) if can_manage else {},
        adjustment_hints=adjustment_hints,
        adjustment_provenance=adjustment_provenance,
        adjustment_history=adjustment_history,
        adjustment_revision=max((entry.id for entry in adjustments.values()), default=0),
        can_delete=can_manage and not notes and not extract_has_reviews(extract.id)
        and not ForecastExperiment.query.filter_by(extract_id=extract.id).first()
        and not ForecastFeatureDecision.query.filter_by(extract_id=extract.id).first()
        and not ForecastReleaseAdjustment.query.filter_by(extract_id=extract.id).first()
        and not ForecastRowExclusion.query.filter_by(extract_id=extract.id).first()
        and not ForecastDataExtract.query.filter_by(derived_from_id=extract.id).first(),
    )


@forecasting_bp.post("/data-readiness/records/<int:record_id>/adjustment")
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_record_adjustment(record_id: int):
    record = ForecastDataRecord.query.get_or_404(record_id)
    action = request.form.get("action", "")
    values = request.form
    rationale = request.form.get("rationale", "")
    reused_from = request.form.get("reused_from_id", type=int)
    if reused_from is not None:
        prior = db.session.get(ForecastReleaseAdjustment, reused_from)
        if prior is None:
            abort(404)
        action, rationale = prior.action, prior.rationale
        values = {key: str(value).lower() if isinstance(value, bool) else str(value)
                  for key, value in prior.corrections.items()}
    try:
        save_adjustment(
            record.id, action, values, rationale, current_user.id,
            request.form.get("capture_identity", ""), request.form.get("expected_id", type=int),
            reused_from_id=reused_from,
        )
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        flash("Adjustment evidence saved. The original extract and calculations are unchanged.", "success")
    return redirect(url_for(
        "forecasting.data_extract", extract_id=record.extract_id, scope="all", release=record.id,
        _anchor=f"record-{record.id}",
    ))


@forecasting_bp.post("/data-readiness/extracts/<int:extract_id>/adjusted")
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_adjusted(extract_id: int):
    try:
        if request.form.get("confirm") != "yes":
            raise ValidationError("Confirm the adjustments before creating an extract.")
        extract = create_adjusted_extract(
            extract_id, request.form.get("capture_identity", ""),
            request.form.get("expected_revision", type=int), current_user.id,
            expected_row_revision=request.form.get("expected_row_revision", 0, type=int),
        )
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("forecasting.data_extract", extract_id=extract_id))
    flash("Adjusted extract created. Original saved data remains unchanged.", "success")
    return redirect(url_for("forecasting.data_extract", extract_id=extract.id))


@forecasting_bp.post("/data-readiness/extracts/<int:extract_id>/row-exclusions")
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_row_exclusions(extract_id: int):
    raw_ids = request.form.getlist("record_id")
    try:
        if request.form.get("confirm") != "yes":
            raise ValidationError("Confirm the selected rows before saving.")
        if any(not value.isascii() or not value.isdecimal() or len(value) > 19 for value in raw_ids):
            raise ValidationError("Choose valid saved rows.")
        record_ids = [int(value) for value in raw_ids]
        if any(not 0 < record_id <= 2**63 - 1 for record_id in record_ids):
            raise ValidationError("Choose valid saved rows.")
        save_row_exclusions(
            extract_id, record_ids, request.form.get("action", ""),
            request.form.get("rationale", ""), current_user.id,
            request.form.get("capture_identity", ""), request.form.get("expected_revision", type=int),
        )
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        flash("Row exclusion evidence saved. The original extract and Epicor are unchanged.", "success")
    return _row_exclusion_redirect(extract_id)


def _row_exclusion_redirect(extract_id):
    return redirect(url_for(
        "forecasting.data_extract", extract_id=extract_id, scope=request.form.get("scope", "findings"),
        finding=request.form.get("finding") or None, page=request.form.get("page", type=int),
        per_page=request.form.get("per_page", type=int), release=request.form.get("release", type=int),
        gbp_group=request.form.get("gbp_group") or None, week=request.form.get("week", type=int),
        year=request.form.get("year", type=int), _anchor="saved-records",
    ))


@forecasting_bp.post("/data-readiness/extracts/<int:extract_id>/confirm-row-exclusions")
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_confirm_row_exclusions(extract_id: int):
    selections = []
    try:
        for value in request.form.getlist("suggestion"):
            parts = value.split(":")
            if len(parts) != 2 or not all(
                part.isascii() and part.isdecimal() and len(part) <= 19 for part in parts
            ):
                raise ValidationError("Select valid suggested exclusions.")
            record_id, prior_id = map(int, parts)
            if not (0 < record_id <= 2**63 - 1 and 0 < prior_id <= 2**63 - 1):
                raise ValidationError("Select valid suggested exclusions.")
            selections.append((record_id, prior_id))
        if request.form.get("confirm") != "yes":
            raise ValidationError("Confirm the suggested exclusions before saving.")
        count = confirm_row_exclusion_suggestions(
            extract_id, selections, current_user.id, request.form.get("capture_identity", ""),
        )
    except NotFoundError:
        db.session.rollback()
        abort(404)
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        flash(f"Confirmed {count} suggested row exclusion(s). Create an adjusted extract to apply them.", "success")
    return _row_exclusion_redirect(extract_id)


def _gbp_review_group(finding: str, values, *, strict: bool = False) -> str | None:
    group = values.get("gbp_group")
    if finding != "nonpositive_value" or "release" in values:
        if strict and group is not None:
            abort(400, description="A GBP group can only filter the nonpositive GBP finding.")
        return None
    if group is not None and group not in NONPOSITIVE_GBP_GROUPS:
        abort(400, description="Choose an available GBP group.")
    return group


def _nonpositive_gbp_groups(extract_id: int) -> dict[str, list[int]]:
    return group_nonpositive_gbp(ForecastDataRecord.query.filter_by(extract_id=extract_id).with_entities(
        ForecastDataRecord.id, ForecastDataRecord.data, ForecastDataRecord.findings,
    ).order_by(ForecastDataRecord.id).yield_per(500))


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/nonpositive-gbp")
@login_required
@permission_required("view_planning")
def nonpositive_gbp(extract_id: int):
    """Explore a saved finding without changing its review outcomes."""
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    if "nonpositive_value" not in extract.summary["rules"]:
        abort(404)
    group = request.args.get("group")
    if group is not None and group not in NONPOSITIVE_GBP_GROUPS:
        abort(400, description="Choose an available GBP group.")
    records = ForecastDataRecord.query.filter_by(extract_id=extract.id)
    groups = _nonpositive_gbp_groups(extract.id)
    per_page = 25
    ids = groups[group] if group is not None else []
    pages = max(1, (len(ids) + per_page - 1) // per_page)
    page = request.args.get("page", type=int) if "page" in request.args else 1
    if page is None or not 1 <= page <= pages:
        abort(404)
    rows = records.filter(ForecastDataRecord.id.in_(ids[(page - 1) * per_page:page * per_page]))
    anchor_id = request.args.get("release", type=int)
    evidence = []
    if "release" in request.args:
        if anchor_id is None or anchor_id not in ids:
            abort(404)
        anchor = records.filter_by(id=anchor_id).first_or_404()
        key_fields = ("order_num", "order_line", "rel_num")
        if all(anchor.data.get(key) is not None for key in key_fields):
            evidence = records.filter(*(
                ForecastDataRecord.data[key].as_integer() == anchor.data[key]
                for key in key_fields
            )).order_by(ForecastDataRecord.id).all()
        else:
            evidence = [anchor]
    return render_template(
        "forecasting/nonpositive_gbp.html", title="Zero and negative values",
        extract=extract, labels=NONPOSITIVE_GBP_GROUPS, groups=groups, group=group,
        rows=rows.order_by(ForecastDataRecord.id).all() if ids else [],
        page=page, pages=pages, evidence=evidence, anchor_id=anchor_id,
    )


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/weekly-intake")
@login_required
@permission_required("view_planning")
def weekly_intake(extract_id: int):
    """Render the weekly net GBP exploration for a single saved extract."""
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    profile = None
    years = {}
    selected_year = None
    compare_years = []
    chart_years = {}
    chart_low = chart_high = None
    fields = extract.source_settings.get("fields", ())
    names: dict[str, set[str]] = {}
    if REQUIRED_FIELDS.issubset(fields):
        history = explore_intake_history(
            _intake_records(extract.id), extract.captured_at.date(),
            period=extract.order_period,
        )
        years = _intake_years(history.weeks)
        available_years = {str(year): year for year in years}
        requested_years = request.args.getlist("compare_year")
        if any(year not in available_years for year in requested_years):
            abort(400, description="Choose comparison years covered by this extract.")
        if requested_years:
            compare_years = sorted({available_years[year] for year in requested_years}, reverse=True)
        elif request.args.get("compare") == "all":
            compare_years = sorted(years, reverse=True)
        if "year" in request.args:
            selected_year = request.args.get("year", type=int)
            if selected_year not in years:
                abort(400, description="Choose a financial year covered by this extract.")
        elif years:
            selected_year = 2026 if 2026 in years else max(years)
        if selected_year is not None:
            profile = explore_weekly_intake(
                _intake_records(extract.id, names if "customer_name" in fields else None),
                extract.captured_at.date(),
                mix_axes=available_mix_axes(fields), year=selected_year,
                period=extract.order_period,
            )
        else:
            profile = history
        if compare_years:
            values = [
                week.net_gbp for year in compare_years for week in years[year]
                if not week.partial and week.net_gbp is not None
            ]
            if values:
                chart_low, chart_high = min(min(values), Decimal(0)), max(max(values), Decimal(0))
                span = chart_high - chart_low
                for year in compare_years:
                    chart_years[year] = [
                        {
                            "week": week,
                            "x": round(70 + (week.number - 1) * 10, 1),
                            "y": round(160 - float((week.net_gbp - chart_low) / span if span else Decimal("0.5")) * 120, 1),
                        }
                        for week in years[year] if not week.partial and week.net_gbp is not None
                    ]
    chart_scale = None
    if profile is not None:
        magnitudes = [
            abs(week.net_gbp) for week in profile.weeks if week.net_gbp is not None
        ]
        if magnitudes:
            chart_scale = max(magnitudes) or 1
    mix_summaries = summarize_mix(profile) if profile is not None else {}
    customer_names = _customer_names(mix_summaries.get("Customer"), "customer_name" in fields, names)
    activity = []
    concentration = customer_concentration(profile) if profile is not None else []
    if profile is not None:
        activity = [
            (week, week.net_gbp / week.orders if week.orders else None)
            for week in profile.weeks if not week.partial and week.net_gbp is not None
        ]
    averages = [average for _, average in activity if average is not None]
    return render_template(
        "forecasting/weekly_intake.html",
        title="Weekly intake",
        extract=extract,
        profile=profile,
        mix_axes=MIX_AXES,
        mix_summaries=mix_summaries,
        customer_names=customer_names,
        customer_names_captured="customer_name" in fields,
        calendar=f"SOI {selected_year}" if selected_year is not None else CALENDAR,
        rules=RULES,
        chart_scale=chart_scale,
        years=years,
        selected_year=selected_year,
        compare_years=compare_years,
        year_styles={year: index for index, year in enumerate(sorted(years, reverse=True))},
        chart_years=chart_years,
        chart_low=chart_low,
        chart_high=chart_high,
        activity=activity,
        activity_scale=max((week.included for week, _ in activity), default=0) or 1,
        average_low=min(min(averages), Decimal(0)) if averages else None,
        average_high=max(max(averages), Decimal(0)) if averages else None,
        concentration=concentration,
        customer_captured="Customer" in available_mix_axes(fields),
    )


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/distribution")
@login_required
@permission_required("view_planning")
def weekly_distribution(extract_id: int):
    """Render distribution and unusual-week sensitivity from a saved extract."""
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    profile = None
    years = {}
    year_distributions = {}
    selected_year = None
    fields = extract.source_settings.get("fields", ())
    if REQUIRED_FIELDS.issubset(fields):
        history = explore_intake_history(
            _intake_records(extract.id), extract.captured_at.date(),
            period=extract.order_period,
        )
        years = _intake_years(history.weeks)
        year_distributions = {
            year: summarize_weekly_distribution(weeks) for year, weeks in years.items()
        }
        if "year" in request.args:
            selected_year = request.args.get("year", type=int)
            if selected_year not in years:
                abort(400, description="Choose a financial year covered by this extract.")
        elif years:
            selected_year = 2026 if 2026 in years else max(years)
        profile = replace(history, weeks=years[selected_year]) if selected_year is not None else history

    distribution = None
    chart_points = []
    chart_min = chart_midpoint = chart_max = median_y = None
    if profile is not None:
        distribution = year_distributions.get(selected_year)
        if distribution is None:
            distribution = summarize_weekly_distribution(profile.weeks)
        if distribution.chart_weeks:
            values = [week.net_gbp for week in distribution.chart_weeks]
            chart_min, chart_max = min(values), max(values)
            chart_midpoint = (chart_min + chart_max) / 2
            span = chart_max - chart_min
            for index, week in enumerate(distribution.chart_weeks):
                position = index / max(len(distribution.chart_weeks) - 1, 1)
                relative_value = (week.net_gbp - chart_min) / span if span else Decimal("0.5")
                chart_points.append({
                    "x": round(76 + float(position) * 536, 1),
                    "y": round(172 - float(relative_value) * 136, 1),
                    "week": week.number,
                    "value": week.net_gbp,
                    "outlier": week.number in distribution.outlier_weeks,
                    "quality_flagged": bool(week.exceptions),
                })
            summary = distribution.unflagged_statistics
            if summary is not None:
                relative_median = (
                    (summary.median - chart_min) / span if span else Decimal("0.5")
                )
                median_y = round(172 - float(relative_median) * 136, 1)

    return render_template(
        "forecasting/distribution.html",
        title="Unusual weeks",
        extract=extract,
        profile=profile,
        distribution=distribution,
        chart_points=chart_points,
        chart_min=chart_min,
        chart_midpoint=chart_midpoint,
        chart_max=chart_max,
        median_y=median_y,
        calendar=f"SOI {selected_year}" if selected_year is not None else CALENDAR,
        selected_year=selected_year,
        year_distributions=year_distributions,
        rules=RULES,
    )


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/weeks/<int:week_number>")
@login_required
@permission_required("view_planning")
def investigate_week(extract_id: int, week_number: int):
    """Trace a completed weekly total to saved releases and their review evidence."""
    extract = ForecastDataExtract.query.get_or_404(extract_id)
    fields = extract.source_settings.get("fields", ())
    if not REQUIRED_FIELDS.issubset(fields):
        abort(400, description="This saved extract lacks required weekly GBP fields.")
    year = _week_year(request.args)
    if year is None or not _week_in_extract(extract, week_number, year):
        abort(404, description="Choose a week covered by the saved extract.")
    calendar = f"SOI {year}"
    scope = request.args.get("scope")
    if scope is not None and scope not in ("all", "included", "voided", "excluded"):
        abort(400, description="Choose an available release filter.")
    page_number = request.args.get("page", type=int) if "page" in request.args else 1
    if page_number is None or page_number < 1:
        abort(400, description="Choose a valid release page.")
    names: dict[str, set[str]] = {}
    profile = explore_weekly_intake(
        _intake_records(extract.id, names if "customer_name" in fields else None),
        extract.captured_at.date(),
        mix_axes=available_mix_axes(fields), detail_week=week_number,
        year=year, period=extract.order_period,
    )
    week = next(week for week in profile.weeks if week.number == week_number)
    distribution = summarize_weekly_distribution(profile.weeks)
    if week.partial and "year" not in request.args:
        abort(404, description="Review partial weeks from the coverage page.")
    scope = scope or ("excluded" if week.exceptions else ("all" if "year" in request.args else "included"))
    details = [
        detail for detail in profile.release_details
        if scope == "all" or detail.status == scope
    ]
    details.sort(key=lambda detail: (
        -(detail.gbp if detail.gbp is not None else Decimal(0)), detail.key,
    ))
    pages = max(1, (len(details) + 24) // 25)
    if page_number > pages:
        return redirect(url_for(
            "forecasting.investigate_week", extract_id=extract.id, week_number=week_number,
            scope=scope, page=pages, _anchor="week-releases",
            year=request.args.get("year", type=int),
        ))
    page_details = details[(page_number - 1) * 25:page_number * 25]
    saved_records = {}
    if page_details:
        release_keys = tuple_(*(
            ForecastDataRecord.data[key].as_integer()
            for key in ("order_num", "order_line", "rel_num")
        ))
        for record in ForecastDataRecord.query.filter(
            ForecastDataRecord.extract_id == extract.id,
            release_keys.in_([detail.key for detail in page_details]),
        ).order_by(ForecastDataRecord.id):
            key = tuple(record.data[name] for name in ("order_num", "order_line", "rel_num"))
            saved_records.setdefault(key, []).append(record)
    mix_summaries = summarize_mix(replace(profile, weeks=[week]))
    customer_names = _customer_names(mix_summaries.get("Customer"), "customer_name" in fields, names)
    return render_template(
        "forecasting/investigate_week.html",
        title=f"Investigate week {week_number}",
        extract=extract, week=week, scope=scope,
        is_outlier=week_number in distribution.outlier_weeks,
        details=page_details, saved_records=saved_records,
        page_number=page_number, pages=pages, matching_count=len(details),
        mix_summaries=mix_summaries, customer_names=customer_names,
        customer_names_captured="customer_name" in fields,
        mix_axes=MIX_AXES, decision_statuses=DECISION_STATUSES,
        notes=ForecastDataNote.query.filter_by(extract_id=extract.id).order_by(
            ForecastDataNote.id.desc(),
        ).all(),
        can_manage=current_user.has_permission("manage_planning"),
        note_week=week_number, note_prefix=f"{calendar} week {week_number}: ",
        week_year=request.args.get("year", type=int), calendar=calendar, rules=RULES,
    )


@forecasting_bp.route("/data-readiness/extracts/<int:extract_id>/notes", methods=["POST"])
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_note(extract_id: int):
    """Append a note without editing the saved observation."""
    note_week = request.form.get("week", type=int)
    year = _week_year(request.form)
    if "week" in request.form:
        extract = ForecastDataExtract.query.get_or_404(extract_id)
        if not _week_in_extract(extract, note_week, year):
            abort(400, description="Choose a week covered by the saved extract.")
    body = request.form.get("body", "").strip()
    if note_week is not None and body:
        body = f"SOI {year} week {note_week}: {body}"
    try:
        save_extract_note(
            extract_id, body, current_user.id,
            request.form.get("capture_identity", ""),
        )
    except NotFoundError:
        abort(404)
    except ValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash("Extract note added.", "success")
    if note_week is not None:
        return redirect(url_for(
            "forecasting.investigate_week", extract_id=extract_id,
            week_number=note_week, year=request.form.get("year", type=int), _anchor="extract-notes",
        ))
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
    investigation_week = request.form.get("week", type=int)
    if "week" in request.form and not _week_in_extract(extract, investigation_week, _week_year(request.form)):
        abort(400, description="Choose a week covered by the saved extract.")
    scope = request.form.get("scope", "findings")
    if scope not in _REVIEW_SCOPES:
        abort(400, description="Choose an available record filter.")
    finding = request.form.get("finding", "")
    if finding and finding not in extract.summary["rules"]:
        abort(400, description="Choose an available finding type.")
    gbp_group = _gbp_review_group(finding, request.form)
    try:
        save_data_decision(
            record.id, request.form.get("status", ""),
            request.form.get("rationale", ""), current_user.id,
            capture_identity=request.form.get("capture_identity", ""),
            remember=request.form.get("remember") == "yes",
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
        gbp_group=gbp_group,
        per_page=request.form.get("per_page", type=int),
        week=investigation_week,
        year=request.form.get("year", type=int),
        release=(
            record.id if request.form.get("release_context") == "1" else None
        ),
        _anchor=f"record-{record.id}",
    ))


@forecasting_bp.post("/data-readiness/extracts/<int:extract_id>/reviews")
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_reviews(extract_id: int):
    payload = request.get_json()
    if not isinstance(payload, dict) or not isinstance(payload.get("capture_identity"), str):
        return jsonify(error="Reopen the saved extract before reviewing."), 400
    items = payload.get("reviews")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_PAGE_REVIEWS:
        return jsonify(error=f"Submit 1 to {MAX_PAGE_REVIEWS} saved rows."), 400
    reviews = []
    for item in items:
        if (
            not isinstance(item, dict)
            or type(item.get("record_id")) is not int
            or not 0 < item["record_id"] <= 2**63 - 1
            or not isinstance(item.get("status"), str)
            or not isinstance(item.get("rationale"), str)
            or type(item.get("remember", False)) is not bool
        ):
            return jsonify(error="Each review must specify a saved row, outcome and evidence."), 400
        reviews.append((item["record_id"], item["status"], item["rationale"], item.get("remember", False)))
    try:
        decisions = save_data_decisions(
            extract_id, reviews, current_user.id, capture_identity=payload["capture_identity"],
        )
    except NotFoundError as exc:
        return jsonify(error=str(exc)), 404
    except ValidationError as exc:
        return jsonify(error=str(exc)), 400
    gbp_counts = (
        _gbp_review_counts(extract_id, _nonpositive_gbp_groups(extract_id))
        if request.args.get("finding") == "nonpositive_value" else None
    )
    return jsonify(counts=_extract_review_counts(extract_id), finding_counts=_finding_review_counts(extract_id), gbp_counts=gbp_counts, reviews=[
        {
            "record_id": decision.record_id,
            "label": DECISION_STATUSES[decision.status],
            "badge": _REVIEW_BADGES[decision.status],
            "status": decision.status,
            "history_entry": render_template(
                "forecasting/_review_entry.html", decision=decision, decision_statuses=DECISION_STATUSES,
            ),
        }
        for decision in decisions
    ])


@forecasting_bp.post("/data-readiness/extracts/<int:extract_id>/confirm-suggestions")
@login_required
@permission_required("view_planning")
@permission_required("manage_planning")
def data_extract_confirm_suggestions(extract_id: int):
    selections = []
    try:
        for value in request.form.getlist("suggestion"):
            parts = value.split(":")
            if len(parts) != 2 or not all(part.isascii() and part.isdecimal() for part in parts):
                raise ValidationError("Select valid suggested reviews.")
            record_id, decision_id = map(int, parts)
            if not (0 < record_id <= 2**63 - 1 and 0 < decision_id <= 2**63 - 1):
                raise ValidationError("Select valid suggested reviews.")
            selections.append((record_id, decision_id))
        if request.form.get("confirm") != "yes":
            raise ValidationError("Confirm the selected reviews before saving.")
        count = confirm_review_suggestions(
            extract_id, selections, current_user.id,
            capture_identity=request.form.get("capture_identity", ""),
        )
    except NotFoundError:
        abort(404)
    except ValidationError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Confirmed {count} previously reviewed row(s). No source values were changed.", "success")
    return redirect(url_for(
        "forecasting.data_extract", extract_id=extract_id, scope="unreviewed",
        page=request.form.get("page", 1, type=int),
        per_page=request.form.get("per_page", type=int), _anchor="saved-records",
    ))


@forecasting_bp.get("/data-readiness/records/<int:record_id>/evidence")
@login_required
@permission_required("view_planning")
def data_record_evidence(record_id: int):
    record = ForecastDataRecord.query.get_or_404(record_id)
    preceding = ForecastDataRecord.query.filter(
        ForecastDataRecord.extract_id == record.extract_id,
        ForecastDataRecord.id < record.id,
        *(ForecastDataRecord.data[key].as_integer() == record.data.get(key)
          for key in ("order_num", "order_line", "rel_num")),
    ).count()
    return redirect(url_for(
        "forecasting.data_extract", extract_id=record.extract_id, scope="all",
        release=record.id, page=preceding // 25 + 1, _anchor=f"record-{record.id}",
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
        title="Product categories",
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
