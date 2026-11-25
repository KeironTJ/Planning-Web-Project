from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import math

import pytest

pytest.importorskip("sklearn", reason="optional forecasting dependencies are not installed")
pytest.importorskip("statsmodels", reason="optional forecasting dependencies are not installed")

from app.forecasting.weekly_intake import IntakeWeek, financial_year, financial_year_start
from scripts.weekly_model_comparison import compare_models


def weekly_data():
    return [
        IntakeWeek(
            number=i, start=date(2026, 1, 5) + timedelta(weeks=i - 1),
            partial=False, possible_shutdown=False,
            net_gbp=Decimal(100 + i * 2 + i * 37 % 17),
        )
        for i in range(1, 28)
    ]


def test_models_fit_only_prior_data_and_share_forecast_targets():
    weeks = weekly_data()
    first = compare_models(weeks, skip_exceptions=False).forecasts[0]
    assert first.week == 25
    assert first.training_rows == 20
    assert first.previous_week == weeks[23].net_gbp
    assert first.four_week_mean == sum(week.net_gbp for week in weeks[20:24]) / 4
    changed = weeks[:]
    changed[24] = replace(changed[24], net_gbp=Decimal(9999))
    changed[25] = replace(changed[25], net_gbp=Decimal(8888))
    new = compare_models(changed, skip_exceptions=False).forecasts[0]
    assert (new.regression, new.arima, new.gradient_boosting, new.gradient_boosting_week) == (
        first.regression, first.arima, first.gradient_boosting, first.gradient_boosting_week,
    )
    assert new.actual == 9999


def test_calendar_model_adds_target_week_to_same_prior_training_rows(monkeypatch):
    models = []

    class RecordingBoost:
        def __init__(self, **settings):
            self.settings = settings
            models.append(self)

        def fit(self, features, outcomes):
            self.features = features
            self.outcomes = outcomes
            return self

        def predict(self, features):
            self.input = features[0]
            return [100.0]

    class ArimaFit:
        mle_retvals = {"converged": True}

        def forecast(self, steps):
            return [1.0]

    class Arima:
        def __init__(self, *args, **kwargs):
            pass

        def fit(self, **kwargs):
            return ArimaFit()

    monkeypatch.setattr("scripts.weekly_model_comparison.GradientBoostingRegressor", RecordingBoost)
    monkeypatch.setattr("scripts.weekly_model_comparison.ARIMA", Arima)
    result = compare_models(weekly_data(), skip_exceptions=False)
    plain, calendar = models[:2]
    assert plain.settings == calendar.settings
    assert plain.outcomes == calendar.outcomes
    assert [row[:-1] for row in calendar.features] == plain.features
    assert [row[-1] for row in calendar.features] == list(range(5, 25))
    assert calendar.input[:-1] == plain.input
    assert calendar.input[-1] == result.forecasts[0].week == 25
    models.clear()
    weeks = []
    for i in range(55):
        start = financial_year_start(2024) + timedelta(weeks=i)
        number = (start - financial_year_start(financial_year(start))).days // 7 + 1
        weeks.append(IntakeWeek(
            number=number, start=start, partial=False, possible_shutdown=False,
            net_gbp=Decimal(100 + i),
        ))
    result = compare_models(weeks, skip_exceptions=False)
    boundary_index = next(i for i, row in enumerate(result.forecasts) if row.week_start == date(2025, 1, 6))
    boundary = models[boundary_index * 2 + 1]
    assert boundary.input[-1] == 1
    assert boundary.features[-1][-1] == 53


def test_flagged_and_missing_weeks_do_not_enter_training_or_forecast_windows():
    weeks = weekly_data()
    weeks[0] = replace(weeks[0], exceptions=1)
    all_weeks = compare_models(weeks, skip_exceptions=False)
    clean = compare_models(weeks, skip_exceptions=True)
    assert all_weeks.forecasts[0].week == 25
    assert clean.forecasts[0].week == 26
    assert clean.forecasts[0].training_rows == 20
    missing = weeks[:]
    missing[0] = replace(missing[0], net_gbp=None, exceptions=0)
    assert compare_models(missing, skip_exceptions=False).forecasts[0].week == 26
    with pytest.raises(ValueError, match="chronological"):
        compare_models(list(reversed(weeks)), skip_exceptions=False)


def test_arima_fit_failure_is_not_reported_as_a_forecast(monkeypatch):
    class FailedFit:
        mle_retvals = {"converged": False}

    class FailedArima:
        def __init__(self, *args, **kwargs):
            pass

        def fit(self, **kwargs):
            return FailedFit()

    monkeypatch.setattr("scripts.weekly_model_comparison.ARIMA", FailedArima)
    with pytest.raises(RuntimeError, match="did not converge for week 25"):
        compare_models(weekly_data(), skip_exceptions=False)


def test_arima_keeps_internal_flagged_weeks_as_missing(monkeypatch):
    observed_series = []

    class Fit:
        mle_retvals = {"converged": True}

        def forecast(self, steps):
            return [100.0]

    class RecordingArima:
        def __init__(self, series, **kwargs):
            observed_series.append(series)

        def fit(self, **kwargs):
            return Fit()

    monkeypatch.setattr("scripts.weekly_model_comparison.ARIMA", RecordingArima)
    weeks = weekly_data()
    weeks.extend([
        replace(weeks[-1], number=i, start=date(2026, 1, 5) + timedelta(weeks=i - 1),
                net_gbp=Decimal(100 + i * 2 + i * 37 % 17))
        for i in range(28, 31)
    ])
    weeks[9] = replace(weeks[9], exceptions=1)
    result = compare_models(weeks, skip_exceptions=True)
    assert [row.week for row in result.forecasts] == [30]
    assert result.forecasts[0].training_rows == 20
    assert len(observed_series[0]) == 29
    assert math.isnan(observed_series[0][9])
    scale = max(float(week.net_gbp) for week in weeks[:29] if week.number != 10)
    assert observed_series[0][10] == float(weeks[10].net_gbp) / scale


def test_model_windows_cross_financial_year_by_date_without_bridging_gaps(monkeypatch):
    class Fit:
        mle_retvals = {"converged": True}

        def forecast(self, steps):
            return [1.0]

    class TestArima:
        def __init__(self, series, **kwargs):
            pass

        def fit(self, **kwargs):
            return Fit()

    monkeypatch.setattr("scripts.weekly_model_comparison.ARIMA", TestArima)
    first = date(2021, 1, 4)
    weeks = []
    for i in range(55):
        start = first + timedelta(weeks=i)
        number = (start - financial_year_start(financial_year(start))).days // 7 + 1
        weeks.append(IntakeWeek(
            number=number, start=start, partial=False, possible_shutdown=False,
            net_gbp=Decimal(100 + i * 2 + i * 37 % 17),
        ))
    result = compare_models(weeks, skip_exceptions=False)
    first_2022 = next(row for row in result.forecasts if row.week_start == date(2022, 1, 3))
    assert first_2022.week == 1
    assert first_2022.previous_week == weeks[51].net_gbp
    assert first_2022.training_rows == 48
    sensitivity = compare_models(weeks, skip_exceptions=False, excluded_weeks=frozenset({first}))
    year_boundary = next(row for row in sensitivity.forecasts if row.week_start == date(2022, 1, 3))
    assert year_boundary.training_rows == 47
    missing = [week for week in weeks if week.start != date(2021, 12, 27)]
    result = compare_models(missing, skip_exceptions=False)
    assert date(2022, 1, 3) not in {row.week_start for row in result.forecasts}
