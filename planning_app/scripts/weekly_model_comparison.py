"""Compare development forecasts on the same saved weekly VA05 observations."""

import argparse
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sklearn import __version__ as sklearn_version
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from statsmodels import __version__ as statsmodels_version
from statsmodels.tsa.arima.model import ARIMA

from app.forecasting.weekly_intake import IntakeWeek, financial_year, summarize_weekly_distribution
from scripts.weekly_intake_review import _show_benchmark, review, score_forecasts


LOOKBACK = 4
MIN_TRAINING_ROWS = 20


@dataclass(frozen=True)
class ModelForecast:
    week: int
    week_start: date
    actual: Decimal
    previous_week: Decimal
    four_week_mean: Decimal
    regression: Decimal
    arima: Decimal
    gradient_boosting: Decimal
    training_rows: int


@dataclass(frozen=True)
class ModelComparison:
    forecasts: list[ModelForecast]
    insufficient_training: int


def compare_models(
    weeks: list[IntakeWeek], *, skip_exceptions: bool,
    excluded_weeks: frozenset[date] = frozenset(),
) -> ModelComparison:
    """Fit each model afresh using only data strictly before each target."""
    if any(
        later.start <= earlier.start
        for earlier, later in zip(weeks, weeks[1:])
    ):
        raise ValueError("Experiment weeks must be in strictly chronological order.")
    if not weeks:
        return ModelComparison([], 0)
    if any(week.start.weekday() != 0 for week in weeks):
        raise ValueError("Experiment week starts must be Mondays.")

    def usable(week: IntakeWeek) -> bool:
        return (
            not week.partial and week.net_gbp is not None
            and week.start not in excluded_weeks
            and (not skip_exceptions or not week.exceptions)
        )

    def value(week: IntakeWeek) -> Decimal:
        if week.net_gbp is None:
            raise ValueError(f"Week {week.number} has no observed net GBP.")
        return week.net_gbp

    by_start = {week.start: week for week in weeks}

    def window(target_start: date) -> list[IntakeWeek] | None:
        starts = [target_start - timedelta(weeks=i) for i in range(LOOKBACK, -1, -1)]
        if any(start not in by_start or not usable(by_start[start]) for start in starts):
            return None
        return [by_start[start] for start in starts]

    forecasts = []
    insufficient = 0
    for target in weeks:
        current = window(target.start)
        if current is None:
            continue
        training = [
            prior_window
            for prior in weeks
            if prior.start < target.start and (prior_window := window(prior.start)) is not None
        ]
        if len(training) < MIN_TRAINING_ROWS:
            insufficient += 1
            continue
        features = [[float(value(week)) for week in row[:-1]] for row in training]
        outcomes = [float(value(row[-1])) for row in training]
        history = [value(week) for week in current[:-1]]
        last_four = [float(amount) for amount in history]

        linear = LinearRegression().fit([[row[-1]] for row in features], outcomes)
        linear_prediction = float(linear.predict([[last_four[-1]]])[0])
        boosted = GradientBoostingRegressor(
            n_estimators=30, learning_rate=0.05, max_depth=2,
            min_samples_leaf=3, random_state=42,
        ).fit(features, outcomes)
        boosted_prediction = float(boosted.predict([last_four])[0])

        scale = max(
            [abs(float(value(week))) for week in weeks if week.start < target.start and usable(week)]
            + [1.0]
        )
        series = [
            float(value(by_start[start])) / scale
            if start in by_start and usable(by_start[start]) else float("nan")
            for start in (
                weeks[0].start + timedelta(weeks=i)
                for i in range((target.start - weeks[0].start).days // 7)
            )
        ]
        fitted = ARIMA(series, order=(1, 1, 0), trend="n").fit(
            method_kwargs={"method": "powell", "maxiter": 500},
        )
        if not fitted.mle_retvals.get("converged", False):
            raise RuntimeError(f"ARIMA did not converge for week {target.number} ({target.start}).")
        arima_prediction = float(fitted.forecast(steps=1)[0]) * scale
        if not all(math.isfinite(prediction) for prediction in (
            linear_prediction, arima_prediction, boosted_prediction,
        )):
            raise ValueError(f"Model returned a non-finite forecast for week {target.number}.")
        forecasts.append(ModelForecast(
            target.number, target.start, value(target), history[-1], sum(history) / LOOKBACK,
            Decimal(str(linear_prediction)), Decimal(str(arima_prediction)),
            Decimal(str(boosted_prediction)), len(training),
        ))
    return ModelComparison(forecasts, insufficient)


def calculate_comparisons(weeks: list[IntakeWeek]) -> tuple[frozenset[date], list[tuple[str, ModelComparison]]]:
    years = sorted({financial_year(week.start) for week in weeks})
    outliers = frozenset(
        week.start
        for year in years
        for year_weeks in [[week for week in weeks if financial_year(week.start) == year]]
        for numbers in [set(summarize_weekly_distribution(year_weeks).outlier_weeks)]
        for week in year_weeks if week.number in numbers and not week.partial and not week.exceptions
    )
    scenarios = [
        ("All usable weeks", False, frozenset()),
        ("Without quality-flagged weeks", True, frozenset()),
        ("Without IQR outlier weeks", False, outliers),
        ("Without quality flags or IQR outliers", True, outliers),
    ]
    return outliers, [
        (label, compare_models(weeks, skip_exceptions=skip, excluded_weeks=excluded))
        for label, skip, excluded in scenarios
    ]


def report_comparison(weeks: list[IntakeWeek]) -> None:
    years = sorted({financial_year(week.start) for week in weeks})
    print("\nDevelopment comparison: one-week-ahead net GBP")
    print(f"scikit-learn {sklearn_version}; statsmodels {statsmodels_version}")
    print(
        f"Expanding training; minimum {MIN_TRAINING_ROWS} complete four-lag training rows; "
        "linear regression on lag 1; ARIMA(1,1,0) without drift, scaled on prior data "
        "and fitted with Powell (max 500 iterations); "
        "gradient boosting on lags 1-4 (30 trees, depth 2, learning rate 0.05, "
        "minimum leaf 3, seed 42)."
    )
    outliers, results = calculate_comparisons(weeks)
    print("IQR outliers identified retrospectively within each financial year: "
          + (", ".join(day.isoformat() for day in sorted(outliers)) or "none"))
    print("Single-capture development data, not a prospective or historical as-of holdout.")
    common = set.intersection(*(set(row.week_start for row in result.forecasts) for _, result in results))

    def show_scores(rows: list[ModelForecast]) -> None:
        for label, attribute in (
            ("Previous week", "previous_week"),
            ("Four-week mean", "four_week_mean"),
            ("Linear regression", "regression"),
            ("ARIMA(1,1,0)", "arima"),
            ("Gradient boosting", "gradient_boosting"),
        ):
            _show_benchmark(label, score_forecasts([
                (getattr(row, attribute), row.actual) for row in rows
            ]))

    for label, result in results:
        print(f"\n{label}")
        print("Forecast target week starts: " + (", ".join(row.week_start.isoformat() for row in result.forecasts) or "none"))
        print(f"Eligible windows with fewer than {MIN_TRAINING_ROWS} training rows: "
              f"{result.insufficient_training}")
        show_scores(result.forecasts)
        for row in result.forecasts:
            print(
                f"Week {row.week_start} (FY{financial_year(row.week_start)} week {row.week}): "
                f"actual {row.actual:.2f}; previous {row.previous_week:.2f}; "
                f"mean {row.four_week_mean:.2f}; linear {row.regression:.2f}; "
                f"ARIMA {row.arima:.2f}; boosting {row.gradient_boosting:.2f} GBP "
                f"({row.training_rows} training rows)"
            )
    print("\nSensitivity comparison on target weeks common to all scenarios: "
          + (", ".join(day.isoformat() for day in sorted(common)) or "none"))
    for label, result in results:
        print(label)
        show_scores([row for row in result.forecasts if row.week_start in common])
    for label, result in results[:2]:
        print(f"\n{label} by financial year:")
        for year in years:
            annual = [row for row in result.forecasts if financial_year(row.week_start) == year]
            if annual:
                print(f"FY{year}")
                show_scores(annual)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Local SQLite database (read-only)")
    parser.add_argument("--extract-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Verified weekly CSV path")
    parser.add_argument("--history", action="store_true", help="Use all saved financial years")
    args = parser.parse_args()
    try:
        weeks = review(args.db, args.extract_id, args.output, history=args.history)
        report_comparison(weeks)
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Model comparison failed: {exc}\n")


if __name__ == "__main__":
    main()
