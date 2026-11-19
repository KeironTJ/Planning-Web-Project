"""Explore weekly GBP from a saved VA05 extract without reading live orders."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


CALENDAR = "SOI 2026"
RULES = "weekly-net-gbp-v1"
FIRST_WEEK = date(2026, 1, 5)
WEEK = timedelta(days=7)
WEEK_COUNT = 52
END = FIRST_WEEK + WEEK_COUNT * WEEK
REQUIRED_FIELDS = frozenset({
    "order_num", "order_line", "rel_num", "assembly_seq", "order_date",
    "release_price_gbp", "void_line",
})


@dataclass
class IntakeWeek:
    number: int
    start: date
    partial: bool
    possible_shutdown: bool
    releases: int = 0
    included: int = 0
    voided: int = 0
    exceptions: int = 0
    net_gbp: Decimal | None = None
    void_gbp: Decimal = Decimal(0)

    @property
    def end(self) -> date:
        return self.start + timedelta(days=6)


@dataclass
class WeeklyIntake:
    weeks: list[IntakeWeek]
    issues: Counter[str]
    outside_calendar: int


def _order_date(value: object) -> date | None:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def _money(value: object) -> Decimal | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        try:
            amount = Decimal(value)
        except InvalidOperation:
            return None
        if amount.is_finite():
            return amount
    return None


def explore_weekly_intake(
    rows: Iterable[dict], captured_on: date,
) -> WeeklyIntake:
    """Bucket one consistent main observation per release by Epicor order date."""
    weeks = [
        IntakeWeek(
            number=i + 1,
            start=start,
            partial=start + WEEK > captured_on,
            possible_shutdown=start >= date(2026, 12, 21),
        )
        for i in range(WEEK_COUNT)
        if (start := FIRST_WEEK + i * WEEK) <= captured_on
    ]
    groups: defaultdict[tuple, list[tuple[object, bool, object, object]]] = defaultdict(list)
    issues: Counter[str] = Counter()
    outside_calendar = 0
    for row in rows:
        key = tuple(row.get(field) for field in ("order_num", "order_line", "rel_num"))
        if any(value is None for value in key):
            issues["Rows with incomplete release keys"] += 1
            continue
        groups[key].append((
            row.get("order_date"), row.get("assembly_seq") in (None, 0),
            row.get("release_price_gbp"), row.get("void_line"),
        ))

    for release in groups.values():
        main = [row for row in release if row[1]]
        dates = {_order_date(row[0]) for row in (main or release)}
        order_date = next(iter(dates)) if len(dates) == 1 else None
        if order_date is None:
            issues["Releases with missing or conflicting order dates"] += 1
            continue
        if order_date < FIRST_WEEK or order_date >= END:
            outside_calendar += 1
            continue
        if order_date > captured_on:
            issues["Releases dated after the capture"] += 1
            continue
        week = weeks[(order_date - FIRST_WEEK).days // 7]
        week.releases += 1
        amounts = {_money(row[2]) for row in main}
        flags = {row[3] if type(row[3]) is bool else None for row in main}
        amount = next(iter(amounts)) if len(amounts) == 1 else None
        problems = []
        if not main:
            problems.append("Releases without a main observation")
        else:
            if amount is None:
                problems.append("Releases with missing or conflicting GBP")
            if len(flags) != 1 or None in flags:
                problems.append("Releases with missing or conflicting void flags")
        if problems:
            issues.update(problems)
            week.exceptions += 1
            continue
        assert amount is not None
        if week.net_gbp is None:
            week.net_gbp = Decimal(0)
        if True in flags:
            week.voided += 1
            week.void_gbp += amount
        else:
            week.included += 1
            week.net_gbp += amount

    return WeeklyIntake(weeks, issues, outside_calendar)
