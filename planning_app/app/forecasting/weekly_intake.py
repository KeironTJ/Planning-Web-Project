"""Explore weekly GBP from a saved VA05 extract without reading live orders."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median


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
class WeeklyIntake:
    weeks: list[IntakeWeek]
    issues: Counter[str]
    outside_calendar: int
    mix: dict[str, dict[int, dict[MixKey, MixValue]]] = field(default_factory=dict)


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


def available_mix_axes(fields: Iterable[str]) -> tuple[str, ...]:
    captured = set(fields)
    return tuple(axis for axis, required in MIX_AXES.items() if captured.issuperset(required))


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


def explore_weekly_intake(
    rows: Iterable[dict], captured_on: date, *, mix_axes: tuple[str, ...] = (),
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
    mix_fields = sorted({name for axis in mix_axes for name in MIX_AXES[axis]})
    mix: dict[str, dict[int, dict[MixKey, MixValue]]] = {axis: {} for axis in mix_axes}
    groups: defaultdict[tuple, list[tuple[object, bool, object, object, dict[str, str]]]] = defaultdict(list)
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
            {name: value.strip() for name in mix_fields
             if isinstance(value := row.get(name), str) and value.strip()},
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

    return WeeklyIntake(weeks, issues, outside_calendar, mix)
