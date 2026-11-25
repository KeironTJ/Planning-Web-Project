import runpy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.exceptions import ValidationError
from app.forecasting.data_quality import delete_unreviewed_extract
from app.forecasting.experiments import _execute_experiment, start_experiment
from app.forecasting.models import ForecastDataExtract, ForecastDataRecord, ForecastExperiment
from app.forecasting.weekly_intake import REQUIRED_FIELDS
from .conftest import login


def saved_extract(db_session):
    extract = ForecastDataExtract(
        captured_at=datetime(2026, 11, 25, tzinfo=timezone.utc), source="bskyVA05v1",
        rules_version="test", row_count=0, content_hash="a" * 64,
        source_settings={"capture_environment": "testing", "fields": list(REQUIRED_FIELDS)},
        summary={"rules": {}, "counts": {}, "flagged_rows": 0},
    )
    db_session.add(extract)
    db_session.commit()
    return extract


def sample_result():
    rows = [
        {"week": 1, "week_start": "2021-01-04", "actual": "100", "previous_week": "90",
         "four_week_mean": "110", "regression": "95", "arima": "80",
         "gradient_boosting": "100", "training_rows": 20},
        {"week": 1, "week_start": "2022-01-03", "actual": "200", "previous_week": "190",
         "four_week_mean": "210", "regression": "195", "arima": "250",
         "gradient_boosting": "200", "training_rows": 50},
    ]
    return {
        "analysis_rules": "test", "python": "test", "scikit_learn": "test", "statsmodels": "test",
        "code_sha256": {"test.py": "a" * 64}, "settings": {"lookback": 4},
        "completed_weeks": 2, "unavailable_weeks": [], "quality_flagged_weeks": [],
        "outliers": [], "scenarios": [
            {"label": label, "insufficient_training": 0, "forecasts": rows}
            for label in ("All", "No flags", "No outliers", "Neither")
        ],
    }


def test_run_is_recorded_once_across_requests_and_completion_is_viewable(
    app, db_session, client, planner_user, viewer_user, monkeypatch,
):
    extract = saved_extract(db_session)
    monkeypatch.setattr("app.forecasting.routes.dependencies_available", lambda: True)
    launched = []
    monkeypatch.setattr("app.forecasting.routes.launch_experiment", lambda app, run_id: launched.append(run_id))
    login(client, planner_user.email, "Planner!Pass1234")
    endpoint = f"/ai-tools/experiments/{extract.id}/start"
    response = client.post(endpoint, data={"capture_identity": extract.capture_identity})
    assert response.status_code == 302
    run = db_session.get(ForecastExperiment, launched[0])
    assert (run.status, run.extract_hash) == ("queued", extract.content_hash)
    duplicate = client.post(endpoint, data={"capture_identity": extract.capture_identity}, follow_redirects=True)
    assert b"Another model experiment is already running" in duplicate.data
    assert len(launched) == 1
    another_extract = saved_extract(db_session)
    with pytest.raises(ValidationError, match="already running"):
        start_experiment(another_extract.id, another_extract.capture_identity, planner_user.id)
    app.config["APP_ENVIRONMENT"] = "production"
    try:
        assert client.post(endpoint, data={"capture_identity": extract.capture_identity}).status_code == 403
    finally:
        app.config["APP_ENVIRONMENT"] = "testing"
    with pytest.raises(ValidationError, match="model experiments"):
        delete_unreviewed_extract(extract.id, planner_user.id, extract.capture_identity)
    monkeypatch.setattr("app.forecasting.experiments._result_for_extract", lambda extract: sample_result())
    _execute_experiment(app, run.id)
    db_session.expire_all()
    assert db_session.get(ForecastExperiment, run.id).status == "completed"
    assert db_session.get(ForecastExperiment, run.id).active_key is None

    client.get("/auth/logout")
    login(client, viewer_user.email, "Viewer!Pass1234")
    result = client.get(f"/ai-tools/workflow/experiments?extract_id={extract.id}&run_id={run.id}")
    assert result.status_code == 200
    html = result.get_data(as_text=True)
    assert "Development evidence only" in html
    assert f"Run {run.id} — {run.requested_at.strftime('%d/%m/%Y %H:%M:%S')} UTC — completed" in html
    assert "23.33%" in html
    assert "03/01/2022" in html
    assert "<circle " in html
    assert f"/ai-tools/data-readiness/extracts/{extract.id}/weeks/1?year=2022" in html
    assert "test.py" in html and extract.content_hash in html
    assert "Gradient boosting + financial week" not in html
    assert client.get(
        f"/ai-tools/workflow/experiments?extract_id={extract.id}&model=gradient_boosting_week"
    ).status_code == 400
    result_data = sample_result()
    result_data["settings"]["boosting_week"] = "four lags plus financial week"
    for row in result_data["scenarios"][0]["forecasts"]:
        row["gradient_boosting_week"] = row["gradient_boosting"]
    run.result = result_data
    db_session.commit()
    new_result = client.get(
        f"/ai-tools/workflow/experiments?extract_id={extract.id}&model=gradient_boosting_week"
    )
    assert new_result.status_code == 200
    assert b"Gradient boosting + financial week" in new_result.data
    filtered = client.get(f"/ai-tools/workflow/experiments?extract_id={extract.id}&run_id={run.id}&year=2021")
    assert b"20.00%" in filtered.data and b"03/01/2022" not in filtered.data
    assert client.get(f"/ai-tools/workflow/experiments?extract_id={extract.id}&run_id={run.id}&year=").status_code == 200
    assert client.get(f"/ai-tools/workflow/experiments?extract_id={extract.id}&model=invalid").status_code == 400
    assert client.post(endpoint, data={"capture_identity": extract.capture_identity}).status_code == 403


def test_failed_or_interrupted_runs_release_the_cross_worker_lock(
    app, db_session, planner_user, monkeypatch,
):
    extract = saved_extract(db_session)
    first = start_experiment(extract.id, extract.capture_identity, planner_user.id)
    monkeypatch.setattr("app.forecasting.experiments._result_for_extract",
                        lambda extract: (_ for _ in ()).throw(ValueError("failed fit")))
    _execute_experiment(app, first.id)
    db_session.expire_all()
    first = db_session.get(ForecastExperiment, first.id)
    assert first.status == "failed" and first.active_key is None
    second = start_experiment(extract.id, extract.capture_identity, planner_user.id)
    second.requested_at = datetime.now(timezone.utc) - timedelta(hours=7)
    db_session.commit()
    third = start_experiment(extract.id, extract.capture_identity, planner_user.id)
    db_session.expire_all()
    assert db_session.get(ForecastExperiment, second.id).status == "failed"
    assert third.status == "queued" and third.active_key == "active"
    _execute_experiment(app, second.id)
    db_session.expire_all()
    assert db_session.get(ForecastExperiment, second.id).status == "failed"
    assert db_session.get(ForecastExperiment, third.id).status == "queued"


def test_performance_history_scores_only_common_unchanged_actuals(
    db_session, client, planner_user, viewer_user,
):
    baseline_extract = saved_extract(db_session)
    candidate_extract = saved_extract(db_session)
    candidate_extract.content_hash = "b" * 64
    before = sample_result()
    after = sample_result()
    for scenario in after["scenarios"]:
        scenario["forecasts"] = [
            {**scenario["forecasts"][0], "actual": "120"},
            {**scenario["forecasts"][1], "previous_week": "180"},
            {**scenario["forecasts"][1], "week_start": "2023-01-02", "actual": "300"},
        ]
    now = datetime.now(timezone.utc)
    first = ForecastExperiment(
        extract_id=baseline_extract.id, extract_hash=baseline_extract.content_hash,
        requested_at=now, finished_at=now, status="completed", result=before,
    )
    second = ForecastExperiment(
        extract_id=candidate_extract.id, extract_hash=candidate_extract.content_hash,
        requested_at=now, finished_at=now, status="completed", result=after,
    )
    db_session.add_all([first, second])
    db_session.commit()
    endpoint = "/ai-tools/experiments/history"
    assert client.get(endpoint).status_code == 302
    login(client, viewer_user.email, "Viewer!Pass1234")
    response = client.get(endpoint, query_string={
        "baseline": first.id, "candidate": second.id, "scenario": "No flags",
        "baseline_model": "previous_week", "candidate_model": "previous_week",
    })
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Shared dates: 2" in html and "Different actual values: 1 excluded" in html
    assert "Candidate-only weeks: 1" in html and "Different saved extract hashes" in html
    matched = html.split("Results on matching target weeks", 1)[1].split("</section>", 1)[0]
    assert "5.00%" in matched and "10.00%" in matched
    assert f"run_id={first.id}" in html and f"run_id={second.id}" in html
    assert client.get(endpoint, query_string={"baseline": 999999}).status_code == 400
    assert client.get(endpoint, query_string={"baseline_model": "invalid"}).status_code == 400
    assert client.get(endpoint, query_string={"scenario": "invalid"}).status_code == 400

    unavailable = client.get(endpoint, query_string={
        "baseline_model": "gradient_boosting_week",
    }).get_data(as_text=True)
    assert "not in selected run" in unavailable and "No forecasts available" in unavailable
    after["analysis_rules"] = "different"
    second.result = after
    db_session.commit()
    assert "saved analysis rules differ" in client.get(endpoint).get_data(as_text=True)


def test_performance_history_empty_and_single_run(db_session, client, viewer_user):
    login(client, viewer_user.email, "Viewer!Pass1234")
    endpoint = "/ai-tools/experiments/history"
    assert "No completed comparisons" in client.get(endpoint).get_data(as_text=True)
    extract = saved_extract(db_session)
    now = datetime.now(timezone.utc)
    run = ForecastExperiment(
        extract_id=extract.id, extract_hash=extract.content_hash,
        requested_at=now, finished_at=now, status="completed", result=sample_result(),
    )
    db_session.add(run)
    db_session.commit()
    response = client.get(endpoint)
    assert response.status_code == 200
    assert "Same saved extract hash" in response.get_data(as_text=True)


def test_real_model_run_saves_the_same_predictions_as_cli(app, db_session, planner_user):
    pytest.importorskip("sklearn", reason="optional forecasting dependencies are not installed")
    pytest.importorskip("statsmodels", reason="optional forecasting dependencies are not installed")
    from app.forecasting.weekly_intake import explore_intake_history
    from scripts.weekly_model_comparison import compare_models

    extract = saved_extract(db_session)
    observations = []
    for i in range(36):
        order_date = datetime(2025, 1, 6) + timedelta(weeks=i)
        row = {
            "order_num": i + 1, "order_line": 1, "rel_num": 1, "assembly_seq": 0,
            "order_date": order_date.date().isoformat(),
            "release_price_gbp": str(250000 + i * 1200 + i * 15397 % 32000),
            "void_line": False,
        }
        observations.append(row)
        db_session.add(ForecastDataRecord(
            extract_id=extract.id, source_row_id=i + 1, data=row, findings=[], has_findings=False,
        ))
    extract.row_count = len(observations)
    db_session.commit()
    run = start_experiment(extract.id, extract.capture_identity, planner_user.id)
    _execute_experiment(app, run.id)
    db_session.expire_all()
    run = db_session.get(ForecastExperiment, run.id)
    assert run.status == "completed", run.error
    weeks = explore_intake_history(observations, extract.captured_at.date()).weeks
    expected = compare_models([week for week in weeks if not week.partial], skip_exceptions=False)
    saved = run.result["scenarios"][0]["forecasts"]
    assert len(saved) == len(expected.forecasts) > 0
    assert saved[0]["week_start"] == expected.forecasts[0].week_start.isoformat()
    assert saved[0]["arima"] == str(expected.forecasts[0].arima)
    assert saved[0]["gradient_boosting_week"] == str(expected.forecasts[0].gradient_boosting_week)
    assert run.result["code_sha256"]["weekly_model_comparison.py"]


def test_experiment_migration_round_trip():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "be49c128a671_add_forecast_experiments.py"
    migration = runpy.run_path(str(path))
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE forecast_data_extracts (id INTEGER PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()
            assert "forecast_experiments" in sa.inspect(conn).get_table_names()
            migration["downgrade"]()
            assert "forecast_experiments" not in sa.inspect(conn).get_table_names()
