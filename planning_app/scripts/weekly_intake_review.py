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

from app.forecasting.weekly_intake import (
    CALENDAR,
    FIRST_WEEK,
    REQUIRED_FIELDS,
    RULES,
    IntakeWeek,
    explore_weekly_intake,
)


SQL_PATH = Path(__file__).with_name("verify_weekly_intake.sql")
UNIT = Decimal("0.0001")


@dataclass(frozen=True)
class Benchmark:
    pairs: int
    mae: Decimal
    wape: Decimal | None
    bias: Decimal


def previous_week_benchmark(
    weeks: list[IntakeWeek], *, skip_exceptions: bool,
) -> Benchmark | None:
    errors = []
    actual_volume = Decimal(0)
    for previous, actual in zip(weeks, weeks[1:]):
        if actual.number != previous.number + 1 or previous.partial or actual.partial:
            continue
        if previous.net_gbp is None or actual.net_gbp is None:
            continue
        if skip_exceptions and (previous.exceptions or actual.exceptions):
            continue
        errors.append(previous.net_gbp - actual.net_gbp)
        actual_volume += abs(actual.net_gbp)
    if not errors:
        return None
    absolute_error = sum(map(abs, errors))
    return Benchmark(
        len(errors), absolute_error / len(errors),
        100 * absolute_error / actual_volume if actual_volume else None,
        sum(errors) / len(errors),
    )


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


def review(database: Path, extract_id: int, output: Path) -> list[IntakeWeek]:
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
        profile = explore_weekly_intake((json.loads(raw) for (raw,) in records), captured.date())
        weeks = [week for week in profile.weeks if not week.partial]
        if not weeks:
            raise ValueError(f"No completed weeks in the {CALENDAR} calendar for this extract.")
        verify_weeks(db, extract_id, weeks)

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
    return weeks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Local SQLite database (read-only)")
    parser.add_argument("--extract-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True, help="CSV path outside version control")
    args = parser.parse_args()
    try:
        weeks = review(args.db, args.extract_id, args.output)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Weekly intake review failed: {exc}\n")
    for label, skip in (("All usable pairs", False), ("Without flagged weeks", True)):
        result = previous_week_benchmark(weeks, skip_exceptions=skip)
        if result is None:
            print(f"{label}: no adjacent usable weeks.")
        else:
            wape = f"{result.wape:.2f}%" if result.wape is not None else "unavailable"
            print(f"{label}: {result.pairs} forecasts; MAE {result.mae:.2f} GBP; "
                  f"WAPE {wape}; bias {result.bias:.2f} GBP")


if __name__ == "__main__":
    main()
