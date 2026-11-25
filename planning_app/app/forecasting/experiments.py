"""Persist and run development comparisons without relying on worker memory."""

import hashlib
import importlib.util
import json
import logging
import platform
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.auth.models import AuditLog
from app.core.exceptions import ValidationError
from app.extensions import db

from .data_quality import _lock_extract
from .models import ForecastDataExtract, ForecastDataRecord, ForecastExperiment
from .weekly_intake import REQUIRED_FIELDS, RULES, explore_intake_history


logger = logging.getLogger(__name__)
TIMEOUT = timedelta(hours=6)


def dependencies_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("sklearn", "statsmodels"))


def start_experiment(extract_id: int, identity: str, user_id: int | None) -> ForecastExperiment:
    extract = _lock_extract(extract_id, identity)
    if not REQUIRED_FIELDS.issubset(extract.source_settings.get("fields", ())):
        db.session.rollback()
        raise ValidationError("This extract does not contain the fields needed for weekly GBP.")
    now = datetime.now(timezone.utc)
    db.session.execute(
        update(ForecastExperiment)
        .where(
            ForecastExperiment.active_key == "active",
            ForecastExperiment.requested_at < now - TIMEOUT,
        )
        .values(status="failed", active_key=None, finished_at=now,
                error="The run exceeded six hours or was interrupted. Start a new run.")
        .execution_options(synchronize_session=False)
    )
    run = ForecastExperiment(
        extract_id=extract.id, extract_hash=extract.content_hash, requested_by_id=user_id,
        requested_at=now, status="queued", active_key="active",
    )
    db.session.add(run)
    try:
        db.session.flush()
        db.session.add(AuditLog(
            user_id=user_id, action="forecast_experiment_started",
            resource="forecast_data_extract",
            details=json.dumps({"extract_id": extract.id, "experiment_id": run.id}),
        ))
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if (
            constraint == "forecast_experiments_active_key_key"
            or str(exc.orig) == "UNIQUE constraint failed: forecast_experiments.active_key"
        ):
            raise ValidationError("Another model experiment is already running. Wait for it to finish.") from exc
        raise
    return run


def _result_for_extract(extract: ForecastDataExtract) -> dict:
    from scripts import weekly_intake_review, weekly_model_comparison
    from . import weekly_intake

    sources = {
        "weekly_model_comparison.py": weekly_model_comparison.__file__,
        "weekly_intake_review.py": weekly_intake_review.__file__,
        "weekly_intake.py": weekly_intake.__file__,
        "experiments.py": __file__,
    }

    def source_hashes() -> dict:
        return {
            name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for name, path in sources.items()
        }

    hashes = source_hashes()
    query = ForecastDataRecord.query.filter_by(extract_id=extract.id)
    if query.count() != extract.row_count:
        raise ValueError("Saved row count does not match the capture metadata.")
    records = query.with_entities(
        ForecastDataRecord.data,
    ).yield_per(500)
    profile = explore_intake_history(
        (data for (data,) in records), extract.captured_at.date(), period=extract.order_period,
    )
    weeks = [week for week in profile.weeks if not week.partial]
    if not weeks:
        raise ValueError("This extract has no completed financial weeks.")
    outliers, scenarios = weekly_model_comparison.calculate_comparisons(weeks)
    if not scenarios[0][1].forecasts:
        raise ValueError("Not enough usable history for a model comparison.")
    results = []
    for label, comparison in scenarios:
        forecasts = []
        for item in comparison.forecasts:
            row = asdict(item)
            row["week_start"] = item.week_start.isoformat()
            for key in (
                "actual", "previous_week", "four_week_mean", "regression", "arima",
                "gradient_boosting", "gradient_boosting_week",
            ):
                row[key] = str(row[key])
            forecasts.append(row)
        results.append({
            "label": label, "insufficient_training": comparison.insufficient_training,
            "forecasts": forecasts,
        })
    if source_hashes() != hashes:
        raise ValueError("Experiment source files changed during the run; rerun it.")
    return {
        "analysis_rules": RULES,
        "python": platform.python_version(),
        "scikit_learn": weekly_model_comparison.sklearn_version,
        "statsmodels": weekly_model_comparison.statsmodels_version,
        "code_sha256": hashes,
        "settings": {
            "minimum_training_rows": weekly_model_comparison.MIN_TRAINING_ROWS,
            "lookback": weekly_model_comparison.LOOKBACK,
            "linear": "One-lag linear regression",
            "arima": "ARIMA(1,1,0), no drift, prior-only scaling, Powell maxiter 500",
            "boosting": "Four lags, 30 trees, depth 2, learning rate 0.05, min leaf 3, seed 42",
            "boosting_week": "Same boosting settings and four lags plus target financial week number (1-53)",
        },
        "completed_weeks": len(weeks),
        "unavailable_weeks": [week.start.isoformat() for week in weeks if week.net_gbp is None],
        "quality_flagged_weeks": [week.start.isoformat() for week in weeks if week.exceptions],
        "outliers": [day.isoformat() for day in sorted(outliers)],
        "scenarios": results,
    }


def _execute_experiment(app, run_id: int) -> None:
    with app.app_context():
        try:
            claimed = db.session.execute(
                update(ForecastExperiment)
                .where(ForecastExperiment.id == run_id, ForecastExperiment.status == "queued")
                .values(status="running")
            ).rowcount
            db.session.commit()
            if claimed != 1:
                return
            run = db.session.get(ForecastExperiment, run_id)
            extract = db.session.get(ForecastDataExtract, run.extract_id)
            if extract is None or extract.content_hash != run.extract_hash:
                raise ValueError("The saved extract is unavailable or its identity changed.")
            result = _result_for_extract(extract)
            db.session.execute(
                update(ForecastExperiment)
                .where(ForecastExperiment.id == run_id, ForecastExperiment.status == "running")
                .values(status="completed", active_key=None, finished_at=datetime.now(timezone.utc),
                        result=result)
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception("Forecast experiment %d failed", run_id)
            reason = (
                "Forecasting packages are not installed. See the experiment setup instructions."
                if isinstance(exc, ImportError) else
                "The experiment failed. Check the application logs before retrying."
            )
            db.session.execute(
                update(ForecastExperiment)
                .where(ForecastExperiment.id == run_id, ForecastExperiment.status.in_(("queued", "running")))
                .values(status="failed", active_key=None, finished_at=datetime.now(timezone.utc), error=reason)
            )
            db.session.commit()


def launch_experiment(app, run_id: int) -> None:
    threading.Thread(
        target=_execute_experiment, args=(app, run_id),
        name=f"forecast-experiment-{run_id}", daemon=True,
    ).start()
