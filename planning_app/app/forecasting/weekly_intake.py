"""Explore weekly GBP from a saved VA05 extract without reading live orders."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median, quantiles


WEEK = timedelta(days=7)
RULES = "weekly-net-gbp-v1"


def financial_year_start(year: int) -> date:
    january_first = date(year, 1, 1)
    return january_first + timedelta(days=(-january_first.weekday()) % 7)


def financial_year(day: date) -> int:
    return day.year - (day < financial_year_start(day.year))


def financial_month(week: int) -> int:
    if week == 53:
        return 12
    quarter, offset = divmod(week - 1, 13)
    return quarter * 3 + (1 if offset < 4 else 2 if offset < 8 else 3)


CALENDAR = "SOI 2026"
FIRST_WEEK = financial_year_start(2026)
END = financial_year_start(2027)
WEEK_COUNT = (END - FIRST_WEEK) // WEEK
REQUIRED_FIELDS = frozenset({
    "order_num", "order_line", "rel_num", "assembly_seq", "order_date",
    "release_price_gbp", "void_line",
})
MIX_AXES = {
    "Product model": ("model",),
    "Size within model": ("model", "size_desc"),
    "Production size within model": ("model", "prod_size"),
    "Epicor customer group": ("customer_group",),
}
MixKey = tuple[str, tuple[str, ...]]


@dataclass
class MixValue:
    releases: int = 0
    gbp: Decimal = Decimal(0)


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
class IntakeRelease:
    key: tuple
    status: str
    gbp: Decimal | None
    reasons: tuple[str, ...]


@dataclass
class WeeklyIntake:
    weeks: list[IntakeWeek]
    issues: Counter[str]
    outside_calendar: int
    mix: dict[str, dict[int, dict[MixKey, MixValue]]] = field(default_factory=dict)
    release_details: list[IntakeRelease] = field(default_factory=list)


@dataclass(frozen=True)
class MixSummary:
    categories: int
    observed_weeks: int
    median_active_weeks: int | float | None
    single_week_categories: int
    top_share: Decimal | None
    missing: MixValue
    conflicting: MixValue
    leaders: list[tuple[tuple[str, ...], MixValue, int]]
    peak_week: int | None
    peak_leaders: list[tuple[tuple[str, ...], Decimal]]


@dataclass(frozen=True)
class DistributionStatistics:
    count: int
    mean: Decimal
    median: Decimal
    minimum: Decimal
    maximum: Decimal
    zero_weeks: int


@dataclass(frozen=True)
class DistributionComparison:
    label: str
    statistics: DistributionStatistics | None


@dataclass(frozen=True)
class WeeklyDistribution:
    completed_weeks: int
    missing_weeks: tuple[int, ...]
    partial_weeks: tuple[int, ...]
    quality_flagged_weeks: tuple[IntakeWeek, ...]
    chart_weeks: tuple[IntakeWeek, ...]
    outlier_weeks: tuple[int, ...]
    unflagged_statistics: DistributionStatistics | None
    comparisons: tuple[DistributionComparison, ...]


def available_mix_axes(fields: Iterable[str]) -> tuple[str, ...]:
    captured = set(fields)
    return tuple(axis for axis, required in MIX_AXES.items() if captured.issuperset(required))


def _distribution_statistics(weeks: Iterable[IntakeWeek]) -> DistributionStatistics | None:
    values = [week.net_gbp for week in weeks if week.net_gbp is not None]
    if not values:
        return None
    return DistributionStatistics(
        count=len(values),
        mean=sum(values, Decimal(0)) / len(values),
        median=median(values),
        minimum=min(values),
        maximum=max(values),
        zero_weeks=sum(value == 0 for value in values),
    )


def summarize_weekly_distribution(weeks: Iterable[IntakeWeek]) -> WeeklyDistribution:
    """Summarize complete observed weeks and sensitivity sets without filling gaps."""
    source_weeks = list(weeks)
    completed = [week for week in source_weeks if not week.partial]
    observed = [week for week in completed if week.net_gbp is not None]
    quality_flagged = [week for week in completed if week.exceptions]
    unflagged = [week for week in observed if not week.exceptions]

    outliers: tuple[int, ...] = ()
    if len(unflagged) >= 4:
        values = [week.net_gbp for week in unflagged]
        low, _, high = quantiles(values, n=4, method="inclusive")
        fence = Decimal("1.5") * (high - low)
        outliers = tuple(
            week.number for week in unflagged
            if not low - fence <= week.net_gbp <= high + fence
        )

    outlier_set = set(outliers)
    quality_set = {week.number for week in quality_flagged}
    comparisons = (
        DistributionComparison(
            "All completed observed weeks",
            _distribution_statistics(observed),
        ),
        DistributionComparison(
            "Without statistical outlier weeks",
            _distribution_statistics(week for week in observed if week.number not in outlier_set),
        ),
        DistributionComparison(
            "Without data-quality flagged weeks",
            _distribution_statistics(unflagged),
        ),
        DistributionComparison(
            "Without outlier or data-quality flagged weeks",
            _distribution_statistics(
                week for week in observed
                if week.number not in outlier_set and week.number not in quality_set
            ),
        ),
    )
    return WeeklyDistribution(
        completed_weeks=len(completed),
        missing_weeks=tuple(week.number for week in completed if week.net_gbp is None),
        partial_weeks=tuple(week.number for week in source_weeks if week.partial),
        quality_flagged_weeks=tuple(quality_flagged),
        chart_weeks=tuple(sorted(observed, key=lambda week: (week.net_gbp, week.number))),
        outlier_weeks=outliers,
        unflagged_statistics=_distribution_statistics(unflagged),
        comparisons=comparisons,
    )


def summarize_mix(profile: WeeklyIntake) -> dict[str, MixSummary]:
    """Reconcile and summarize mix for completed financial weeks."""
    completed = [week for week in profile.weeks if not week.partial]
    observed = [week for week in completed if week.net_gbp is not None]
    peak = max(observed, key=lambda week: week.net_gbp or Decimal(0), default=None)
    summaries = {}
    for axis, by_week in profile.mix.items():
        categories: defaultdict[tuple[str, ...], MixValue] = defaultdict(MixValue)
        active_weeks: Counter[tuple[str, ...]] = Counter()
        unresolved = {"missing": MixValue(), "conflicting": MixValue()}
        total = Decimal(0)
        for week in completed:
            buckets = by_week.get(week.number, {})
            if (
                sum(value.releases for value in buckets.values()) != week.included
                or sum(value.gbp for value in buckets.values()) != (week.net_gbp or 0)
            ):
                raise ValueError(f"{axis} does not reconcile for financial week {week.number}.")
            for (status, label), value in buckets.items():
                target = categories[label] if status == "assigned" else unresolved[status]
                target.releases += value.releases
                target.gbp += value.gbp
                if status == "assigned":
                    active_weeks[label] += 1
                total += value.gbp
        top = sorted(categories, key=lambda label: (-categories[label].gbp, label))[:5]
        peak_categories = [
            (label, value.gbp) for (status, label), value in by_week.get(peak.number, {}).items()
            if status == "assigned"
        ] if peak else []
        summaries[axis] = MixSummary(
            categories=len(categories),
            observed_weeks=len(observed),
            median_active_weeks=median(active_weeks.values()) if active_weeks else None,
            single_week_categories=sum(count == 1 for count in active_weeks.values()),
            top_share=100 * sum(categories[label].gbp for label in top) / total if total > 0 else None,
            missing=unresolved["missing"],
            conflicting=unresolved["conflicting"],
            leaders=[(label, categories[label], active_weeks[label]) for label in top],
            peak_week=peak.number if peak else None,
            peak_leaders=sorted(peak_categories, key=lambda entry: (-entry[1], entry[0]))[:3],
        )
    return summaries


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


def main_release_value(
    observations: Iterable[tuple[object, object]],
) -> tuple[Decimal | None, bool | None]:
    """Resolve GBP and void status independently from the main observations."""
    amounts: set[Decimal | None] = set()
    flags: set[bool | None] = set()
    for raw_gbp, raw_void in observations:
        amounts.add(_money(raw_gbp))
        flags.add(raw_void if isinstance(raw_void, bool) else None)
    return (
        next(iter(amounts)) if len(amounts) == 1 else None,
        next(iter(flags)) if len(flags) == 1 else None,
    )


def explore_weekly_intake(
    rows: Iterable[dict], captured_on: date, *, mix_axes: tuple[str, ...] = (),
    detail_week: int | None = None, year: int = 2026,
    period: tuple[date, date] | None = None,
) -> WeeklyIntake:
    """Bucket one consistent main observation per release by Epicor order date."""
    return _explore_intake(
        rows, captured_on, mix_axes=mix_axes, detail_week=detail_week, year=year, period=period,
    )


def explore_intake_history(
    rows: Iterable[dict], captured_on: date, *, period: tuple[date, date] | None = None,
) -> WeeklyIntake:
    """Keep gaps from the first observed financial year through capture."""
    return _explore_intake(rows, captured_on, year=None, period=period)


def release_order_date(observations: Sequence[tuple[date | None, bool]]) -> date | None:
    """Choose one date from main rows, or from all rows when no main exists."""
    dates = {date for date, main in observations if main} or {date for date, _ in observations}
    return next(iter(dates)) if len(dates) == 1 else None


def _explore_intake(
    rows: Iterable[dict], captured_on: date, *, mix_axes: tuple[str, ...] = (),
    detail_week: int | None = None, year: int | None,
    period: tuple[date, date] | None = None,
) -> WeeklyIntake:
    lower = financial_year_start(year) if year is not None else None
    upper = financial_year_start(year + 1) - timedelta(days=1) if year is not None else captured_on
    if period:
        lower = max(lower, period[0]) if lower is not None else period[0]
        upper = min(upper, period[1])

    def make_week(start: date) -> IntakeWeek:
        year = financial_year(start)
        return IntakeWeek(
            number=(start - financial_year_start(year)) // WEEK + 1,
            start=start,
            partial=(
                start + WEEK > captured_on
                or (period is not None and (start < period[0] or start + timedelta(days=6) > period[1]))
            ),
            possible_shutdown=date(2026, 12, 21) <= start < END,
        )

    weeks_by_start: dict[date, IntakeWeek] = {}
    mix_fields = sorted({name for axis in mix_axes for name in MIX_AXES[axis]})
    mix: dict[str, dict[int, dict[MixKey, MixValue]]] = {axis: {} for axis in mix_axes}
    groups: defaultdict[tuple, list[tuple[object, bool, object, object, dict[str, str]]]] = defaultdict(list)
    issues: Counter[str] = Counter()
    outside_calendar = 0
    release_details = []
    for row in rows:
        key = tuple(row.get(field) for field in ("order_num", "order_line", "rel_num"))
        if any(value is None for value in key):
            issues["Rows with incomplete release keys"] += 1
            continue
        groups[key].append((
            row.get("order_date"), row.get("assembly_seq") in (None, 0),
            row.get("release_price_gbp"), row.get("void_line"),
            {name: value.strip() for name in mix_fields
             if isinstance(value := row.get(name), str) and value.strip()},
        ))

    for key, release in groups.items():
        main = [row for row in release if row[1]]
        order_date = release_order_date([(_order_date(row[0]), row[1]) for row in release])
        if order_date is None:
            issues["Releases with missing or conflicting order dates"] += 1
            continue
        if (lower is not None and order_date < lower) or (year is not None and order_date > upper):
            outside_calendar += 1
            continue
        if order_date > captured_on:
            issues["Releases dated after the capture"] += 1
            continue
        if order_date > upper:
            outside_calendar += 1
            continue
        start = order_date - timedelta(days=order_date.weekday())
        if start not in weeks_by_start:
            weeks_by_start[start] = make_week(start)
        week = weeks_by_start[start]
        week.releases += 1
        amount, voided = main_release_value((row[2], row[3]) for row in main)
        problems = []
        if not main:
            problems.append("Releases without a main observation")
        else:
            if amount is None:
                problems.append("Releases with missing or conflicting GBP")
            if voided is None:
                problems.append("Releases with missing or conflicting void flags")
        if week.number == detail_week:
            release_details.append(IntakeRelease(
                key=key,
                status="excluded" if problems else ("voided" if voided else "included"),
                gbp=amount,
                reasons=tuple(problems),
            ))
        if problems:
            issues.update(problems)
            week.exceptions += 1
            continue
        assert amount is not None
        if week.net_gbp is None:
            week.net_gbp = Decimal(0)
        if voided:
            week.voided += 1
            week.void_gbp += amount
        else:
            week.included += 1
            week.net_gbp += amount
            for axis, by_week in mix.items():
                labels = {tuple(row[4].get(name, "") for name in MIX_AXES[axis]) for row in main}
                if len(labels) != 1:
                    bucket: MixKey = ("conflicting", ())
                else:
                    label = next(iter(labels))
                    bucket = ("assigned", label) if all(label) else ("missing", ())
                value = by_week.setdefault(week.number, {}).setdefault(bucket, MixValue())
                value.releases += 1
                value.gbp += amount

    finish = min(upper, captured_on)
    if lower is None:
        if not weeks_by_start:
            return WeeklyIntake([], issues, outside_calendar)
        lower = financial_year_start(financial_year(min(weeks_by_start)))
    if lower > finish:
        return WeeklyIntake([], issues, outside_calendar)
    start = lower - timedelta(days=lower.weekday())
    weeks = []
    while start <= finish:
        weeks.append(weeks_by_start.get(start) or make_week(start))
        start += WEEK
    return WeeklyIntake(weeks, issues, outside_calendar, mix, release_details)
