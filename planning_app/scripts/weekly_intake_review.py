"""Verify, freeze, and benchmark a saved VA05 extract from local SQLite."""

import argparse
import csv
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import correlation, mean, median, quantiles

from app.forecasting.weekly_intake import (
    CALENDAR,
    FIRST_WEEK,
    MIX_AXES,
    REQUIRED_FIELDS,
    RULES,
    IntakeWeek,
    WeeklyIntake,
    available_mix_axes,
    explore_weekly_intake,
    summarize_mix,
)


SQL_PATH = Path(__file__).with_name("verify_weekly_intake.sql")
UNIT = Decimal("0.0001")


@dataclass(frozen=True)
class Benchmark:
    pairs: int
    mae: Decimal
    wape: Decimal | None
    bias: Decimal


@dataclass(frozen=True)
class ForecastComparison:
    week: int
    actual: Decimal
    previous_week: Decimal
    rolling_mean: Decimal


def describe_mix(profile: WeeklyIntake) -> list[str]:
    lines = []
    summaries = summarize_mix(profile)
    for axis in MIX_AXES:
        summary = summaries.get(axis)
        if summary is None:
            lines.append(f"{axis}: unavailable (not captured)")
            continue
        share = summary.top_share
        share_text = f"{share:.1f}%" if share is not None else "unavailable"
        lines.append(f"{axis}: {summary.categories} categories; top-five net GBP share {share_text}")
        if summary.median_active_weeks is not None:
            lines.append(
                f"  Median active weeks: {summary.median_active_weeks:g} of {summary.observed_weeks}; "
                f"categories active in only one week: {summary.single_week_categories}"
            )
        for status, value in (("missing", summary.missing), ("conflicting", summary.conflicting)):
            lines.append(f"  {status}: {value.releases} releases, {value.gbp:,.2f} GBP")
        for label, value, active_weeks in summary.leaders:
            lines.append(
                f"  {' / '.join(label)}: {value.gbp:,.2f} GBP; "
                f"{value.releases} releases in {active_weeks} weeks"
            )
        if summary.peak_leaders:
            details = "; ".join(f"{' / '.join(label)}: {gbp:,.2f} GBP" for label, gbp in summary.peak_leaders)
            lines.append(f"  Peak week {summary.peak_week}, largest known categories: {details}")
    return lines


def describe_weekly_intake(weeks: list[IntakeWeek]) -> list[str]:
    """Describe completed weeks without filling gaps or using flagged totals."""
    completed = [week for week in weeks if not week.partial]
    missing = [week.number for week in completed if week.net_gbp is None]
    flagged = [week.number for week in completed if week.exceptions]
    shutdown = [week.number for week in completed if week.possible_shutdown]
    observed = {
        week.number: week.net_gbp for week in completed
        if week.net_gbp is not None and not week.exceptions
    }

    def labels(numbers: list[int]) -> str:
        return ", ".join(map(str, numbers)) or "none"

    lines = [
        f"EDA coverage: {len(completed)} completed weeks; {len(observed)} unflagged observations",
        f"Unavailable weeks: {labels(missing)}; exception weeks: {labels(flagged)}; "
        f"possible shutdown weeks: {labels(shutdown)}",
    ]
    values = list(observed.values())
    if not values:
        return lines + ["No unflagged weekly values to describe."]
    lines.append(
        f"Net GBP: mean {mean(values):,.2f}; median {median(values):,.2f}; "
        f"range {min(values):,.2f} to {max(values):,.2f}; "
        f"zero weeks {sum(value == 0 for value in values)}"
    )
    outliers = []
    if len(values) >= 4:
        low, _, high = quantiles(values, n=4, method="inclusive")
        fence = Decimal("1.5") * (high - low)
        outliers = [
            week for week, value in observed.items() if not low - fence <= value <= high + fence
        ]
        lines.append(f"IQR outlier candidates: {labels(outliers)}")

    if completed:
        midpoint = completed[len(completed) // 2].number
        first = [value for week, value in observed.items() if week < midpoint]
        second = [value for week, value in observed.items() if week >= midpoint]
        if first and second:
            lines.append(
                f"Mean before week {midpoint}: {mean(first):,.2f} GBP ({len(first)} weeks); "
                f"from week {midpoint}: {mean(second):,.2f} GBP ({len(second)} weeks)"
            )
    def report_lags(series: dict[int, Decimal]) -> None:
        for lag in (1, 2, 4):
            pairs = [(float(value), float(series[week - lag])) for week, value in series.items()
                     if week - lag in series]
            estimate = "unavailable"
            if len(pairs) >= 3:
                actual, lagged = zip(*pairs)
                if len(set(actual)) > 1 and len(set(lagged)) > 1:
                    estimate = f"{correlation(actual, lagged):+.2f}"
            lines.append(f"Lag {lag} Pearson correlation: {estimate} ({len(pairs)} pairs)")

    report_lags(observed)
    if outliers:
        lines.append("Lag sensitivity without IQR outlier candidates:")
        report_lags({week: value for week, value in observed.items() if week not in outliers})
    return lines


def score_forecasts(pairs: list[tuple[Decimal, Decimal]]) -> Benchmark | None:
    """Score (forecast, actual) pairs with positive bias denoting overforecast."""
    if not pairs:
        return None
    errors = [forecast - actual for forecast, actual in pairs]
    actual_volume = sum(abs(actual) for _, actual in pairs)
    absolute_error = sum(map(abs, errors))
    return Benchmark(
        len(errors), absolute_error / len(errors),
        100 * absolute_error / actual_volume if actual_volume else None,
        sum(errors) / len(errors),
    )


def previous_week_benchmark(
    weeks: list[IntakeWeek], *, skip_exceptions: bool,
) -> Benchmark | None:
    pairs = []
    for previous, actual in zip(weeks, weeks[1:]):
        if actual.number != previous.number + 1 or previous.partial or actual.partial:
            continue
        if previous.net_gbp is None or actual.net_gbp is None:
            continue
        if skip_exceptions and (previous.exceptions or actual.exceptions):
            continue
        pairs.append((previous.net_gbp, actual.net_gbp))
    return score_forecasts(pairs)


def compare_forecasts(
    weeks: list[IntakeWeek], *, lookback: int, skip_exceptions: bool,
) -> list[ForecastComparison]:
    """Score both forecasts only where the rolling window and actual are usable."""
    if lookback < 2:
        raise ValueError("The comparison lookback must be at least two weeks.")
    comparisons = []
    for i in range(lookback, len(weeks)):
        target = weeks[i]
        history = weeks[i - lookback:i]
        window = [*history, target]
        if any(week.number != target.number - lookback + j or week.partial
               for j, week in enumerate(window)):
            continue
        if skip_exceptions and any(week.exceptions for week in window):
            continue
        values = [week.net_gbp for week in history if week.net_gbp is not None]
        if len(values) != lookback or target.net_gbp is None:
            continue
        comparisons.append(ForecastComparison(
            target.number, target.net_gbp, values[-1], sum(values) / lookback,
        ))
    return comparisons


def _show_benchmark(label: str, result: Benchmark | None) -> None:
    if result is None:
        print(f"{label}: no adjacent usable weeks.")
        return
    wape = f"{result.wape:.2f}%" if result.wape is not None else "unavailable"
    print(f"{label}: {result.pairs} forecasts; MAE {result.mae:.2f} GBP; "
          f"WAPE {wape}; bias {result.bias:.2f} GBP")


def verify_weeks(db: sqlite3.Connection, extract_id: int, weeks: list[IntakeWeek]) -> None:
    sql_rows = {row[0]: tuple(row[1:]) for row in db.execute(
        SQL_PATH.read_text(encoding="utf-8"),
        {"extract_id": extract_id, "first_week": FIRST_WEEK.isoformat(),
         "end": (weeks[-1].end + timedelta(days=1)).isoformat()},
    )}
    for week in weeks:
        actual = (
            week.releases, week.included, week.voided, week.exceptions,
            week.net_gbp / UNIT if week.net_gbp is not None else None,
            week.void_gbp / UNIT,
        )
        if actual != sql_rows.pop(week.number, (0, 0, 0, 0, None, 0)):
            raise ValueError(f"Independent SQL disagrees for financial week {week.number}.")
    if sql_rows:
        raise ValueError("Independent SQL returned weeks outside the completed series.")


def review(database: Path, extract_id: int, output: Path, *, mix: bool = False) -> list[IntakeWeek]:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only = ON")
        db.execute("BEGIN")
        extract = db.execute(
            """SELECT captured_at, rules_version, content_hash, row_count, source_settings, source
               FROM forecast_data_extracts WHERE id = ?""",
            (extract_id,),
        ).fetchone()
        if extract is None:
            raise ValueError(f"Saved extract {extract_id} does not exist.")
        captured_at, version, digest, count, raw_settings, source = extract
        settings = json.loads(raw_settings)
        if source != "bskyVA05v1" or not REQUIRED_FIELDS.issubset(settings.get("fields", ())):
            raise ValueError("This extract does not contain the fields needed for weekly VA05 GBP.")
        captured = datetime.fromisoformat(captured_at)
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        else:
            captured = captured.astimezone(timezone.utc)
        records = db.execute(
            "SELECT data FROM forecast_data_records WHERE extract_id = ?", (extract_id,),
        ).fetchall()
        if len(records) != count:
            raise ValueError("Saved row count does not match the capture metadata.")
        axes = available_mix_axes(settings["fields"]) if mix else ()
        profile = explore_weekly_intake(
            (json.loads(raw) for (raw,) in records), captured.date(), mix_axes=axes,
        )
        weeks = [week for week in profile.weeks if not week.partial]
        if not weeks:
            raise ValueError(f"No completed weeks in the {CALENDAR} calendar for this extract.")
        verify_weeks(db, extract_id, weeks)
        mix_summary = describe_mix(profile) if mix else []

    prefix = {"development": "DEV", "production": "LIVE", "testing": "TEST"}.get(
        settings.get("capture_environment"), "UNLABELLED",
    )
    rows = [{
        "extract": f"{prefix}-{extract_id}", "captured_at_utc": captured.isoformat().replace("+00:00", "Z"),
        "extract_sha256": digest, "capture_rules": version, "analysis_rules": RULES,
        "calendar": CALENDAR, "financial_week": str(week.number),
        "week_start": week.start.isoformat(), "week_end": week.end.isoformat(),
        "coverage": (
            "unavailable" if week.net_gbp is None
            else "incomplete_excluded_release" if week.exceptions else "observed"
        ),
        "included_releases": str(week.included), "voided_releases": str(week.voided),
        "exceptions": str(week.exceptions),
        "net_gbp": f"{week.net_gbp:.4f}" if week.net_gbp is not None else "",
        "void_gbp": f"{week.void_gbp:.4f}" if week.net_gbp is not None else "",
    } for week in weeks]
    if output.exists():
        with output.open(newline="", encoding="utf-8") as file:
            if list(csv.DictReader(file)) != rows:
                raise ValueError("Output differs from the verified result; refusing to overwrite it.")
    else:
        with output.open("x", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"{prefix}-{extract_id}: {len(weeks)} completed weeks matched independent SQL.")
    print(f"Verified dataset: {output}")
    if mix_summary:
        print("\n".join(mix_summary))
    return weeks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Local SQLite database (read-only)")
    parser.add_argument("--extract-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True, help="CSV path outside version control")
    parser.add_argument("--compare-lookback", type=int, metavar="WEEKS",
                        help="Also compare last week with a trailing mean of WEEKS prior weeks")
    parser.add_argument("--eda", action="store_true", help="Describe coverage, variation and lag relationships")
    parser.add_argument("--mix", action="store_true", help="Review release-level product and customer-group mix")
    args = parser.parse_args()
    if args.compare_lookback is not None and args.compare_lookback < 2:
        parser.error("--compare-lookback must be at least two weeks")
    try:
        weeks = review(args.db, args.extract_id, args.output, mix=args.mix)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Weekly intake review failed: {exc}\n")
    if args.eda:
        print("\n".join(describe_weekly_intake(weeks)))
    for label, skip in (("All usable pairs", False), ("Without flagged weeks", True)):
        _show_benchmark(label, previous_week_benchmark(weeks, skip_exceptions=skip))
    if args.compare_lookback is not None:
        for label, skip in (("All usable windows", False), ("Without flagged windows", True)):
            comparisons = compare_forecasts(
                weeks, lookback=args.compare_lookback, skip_exceptions=skip,
            )
            scored = ", ".join(str(row.week) for row in comparisons)
            print(f"{label} - common forecast weeks: {scored or 'none'}")
            _show_benchmark("Previous week", score_forecasts([
                (row.previous_week, row.actual) for row in comparisons
            ]))
            _show_benchmark(f"Previous {args.compare_lookback}-week mean", score_forecasts([
                (row.rolling_mean, row.actual) for row in comparisons
            ]))
            largest = sorted(
                comparisons, key=lambda row: abs(row.previous_week - row.actual), reverse=True,
            )[:3]
            for row in largest:
                print(f"Week {row.week}: previous-week error "
                      f"{row.previous_week - row.actual:+.2f} GBP; trailing-mean error "
                      f"{row.rolling_mean - row.actual:+.2f} GBP")


if __name__ == "__main__":
    main()
