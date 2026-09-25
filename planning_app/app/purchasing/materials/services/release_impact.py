"""What-if analysis for releasing open purchase-order supply."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from flask import current_app

from app.extensions import cache
from app.sales.orders.models import SalesOrder
from ..models import MaterialRequirementMain, PurchaseOrder
from .loaders import (
    _get_group_class_ids,
    _get_group_lead_days,
    _load_exempt_codes,
    _load_stock,
)

__all__ = ["get_release_impact", "sort_release_impact_results"]

_DIVISORS = {"C": Decimal(100), "M": Decimal(1000)}
_MAX_BUNDLE_CANDIDATES = 18
_MAX_BUNDLE_RESULTS = 25


@dataclass(frozen=True)
class _Requirement:
    row_id: int
    group: str
    material_code: str
    description: str
    works_order: str
    so_number: str | None
    due_date: date | None
    net_required: Decimal


@dataclass(frozen=True)
class _Candidate:
    key: str
    po_num: int
    po_line: int
    po_release: int
    material_code: str
    description: str
    unit_of_measure: str
    supplier_key: str
    supplier: str
    due_date: date
    release_qty: Decimal
    received_qty: Decimal
    outstanding_qty: Decimal
    cash_proxy: Decimal


@dataclass
class _Scenario:
    requirement_shortage: dict[int, Decimal]
    job_shortage: dict[str, Decimal]
    job_blockers: dict[str, set[str]]
    ready_jobs: set[str]
    ready_orders: set[str]


def _load_requirements(include_components: bool = True) -> list[_Requirement]:
    exempt_codes = _load_exempt_codes()
    requirements: list[_Requirement] = []

    groups = ("fabric", "component") if include_components else ("fabric",)
    for group in groups:
        query = MaterialRequirementMain.query.filter(
            MaterialRequirementMain.material_group == group,
            MaterialRequirementMain.job_closed != True,
            MaterialRequirementMain.issued_complete != True,
            MaterialRequirementMain.works_order.isnot(None),
            MaterialRequirementMain.material_code.isnot(None),
        )
        class_ids = _get_group_class_ids(group)
        if class_ids:
            query = query.filter(MaterialRequirementMain.class_id.in_(class_ids))

        for row in query.order_by(MaterialRequirementMain.due_date, MaterialRequirementMain.id).all():
            material_code = row.material_code or ""
            net_required = max(
                Decimal(0),
                (row.qty_for_order or Decimal(0)) - (row.qty_issued or Decimal(0)),
            )
            if not material_code or material_code in exempt_codes or net_required == 0:
                continue
            requirements.append(_Requirement(
                row_id=row.id,
                group=group,
                material_code=material_code,
                description=row.material_description or "",
                works_order=row.works_order or "",
                so_number=row.so_number,
                due_date=row.due_date,
                net_required=net_required,
            ))
    return requirements


def _cash_proxy(po: PurchaseOrder) -> Decimal:
    divisor = _DIVISORS.get(po.cost_per_code or "E", Decimal(1))
    return (
        (po.outstanding_qty or Decimal(0))
        * (po.unit_cost or Decimal(0))
        / divisor
    )


def _load_candidates(
    material_codes: set[str],
    descriptions_by_material: dict[str, str],
) -> list[_Candidate]:
    if not material_codes:
        return []
    rows = (
        PurchaseOrder.query
        .filter(
            PurchaseOrder.part_num.in_(material_codes),
            PurchaseOrder.outstanding_qty > 0,
            PurchaseOrder.due_date.isnot(None),
        )
        .order_by(PurchaseOrder.due_date, PurchaseOrder.po_num)
        .all()
    )
    return [
        _Candidate(
            key=f"{po.po_num}/{po.po_line}/{po.po_release}",
            po_num=po.po_num,
            po_line=po.po_line,
            po_release=po.po_release,
            material_code=po.part_num or "",
            description=(
                po.line_desc
                or descriptions_by_material.get(po.part_num or "", "")
            ),
            unit_of_measure=po.unit_of_measure or "",
            supplier_key=(
                f"id:{po.supplier_id}"
                if po.supplier_id
                else (
                    f"name:{po.supplier_name}"
                    if po.supplier_name
                    else f"po:{po.po_num}"
                )
            ),
            supplier=po.supplier_name or po.supplier_id or "",
            due_date=po.due_date,
            release_qty=po.rel_qty or Decimal(0),
            received_qty=po.received_qty or Decimal(0),
            outstanding_qty=po.outstanding_qty or Decimal(0),
            cash_proxy=_cash_proxy(po),
        )
        for po in rows
    ]


def _simulate(
    requirements: list[_Requirement],
    candidates: list[_Candidate],
    selected_keys: frozenset[str],
    stock_map: dict[str, Decimal],
    lead_days_by_group: dict[str, int],
) -> _Scenario:
    reqs_by_material: dict[tuple[str, str], list[_Requirement]] = defaultdict(list)
    for req in requirements:
        reqs_by_material[(req.group, req.material_code)].append(req)

    selected_by_material: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.key in selected_keys:
            selected_by_material[candidate.material_code].append(candidate)

    requirement_shortage: dict[int, Decimal] = {}
    job_shortage: dict[str, Decimal] = defaultdict(Decimal)
    job_blockers: dict[str, set[str]] = defaultdict(set)
    today = date.today()

    for (group, material_code), reqs in reqs_by_material.items():
        reqs.sort(key=lambda req: (req.due_date or date.max, req.works_order, req.row_id))
        remaining_stock = stock_map.get(material_code, Decimal(0))
        po_lines = sorted(
            selected_by_material.get(material_code, []),
            key=lambda po: (po.due_date, po.po_num, po.po_line, po.po_release),
        )
        po_consumed = Decimal(0)
        lead_days = lead_days_by_group[group]

        for req in reqs:
            deadline = req.due_date - timedelta(days=lead_days) if req.due_date else None
            po_gross = sum(
                (
                    po.outstanding_qty
                    for po in po_lines
                    if deadline is None or max(po.due_date, today) <= deadline
                ),
                Decimal(0),
            )
            po_available = max(Decimal(0), po_gross - po_consumed)
            shortage = max(
                Decimal(0),
                req.net_required - remaining_stock - po_available,
            )
            covered = req.net_required - shortage
            stock_used = min(remaining_stock, covered)
            po_used = min(po_available, covered - stock_used)
            remaining_stock -= stock_used
            po_consumed += po_used

            requirement_shortage[req.row_id] = shortage
            job_shortage[req.works_order] += shortage
            if shortage > 0:
                job_blockers[req.works_order].add(material_code)

    all_jobs = {req.works_order for req in requirements}
    ready_jobs = {job for job in all_jobs if job_shortage.get(job, Decimal(0)) == 0}

    jobs_by_order: dict[str, set[str]] = defaultdict(set)
    for req in requirements:
        if req.so_number:
            jobs_by_order[req.so_number].add(req.works_order)
    ready_orders = {
        so_number
        for so_number, jobs in jobs_by_order.items()
        if jobs and jobs.issubset(ready_jobs)
    }

    return _Scenario(
        requirement_shortage=requirement_shortage,
        job_shortage=dict(job_shortage),
        job_blockers=dict(job_blockers),
        ready_jobs=ready_jobs,
        ready_orders=ready_orders,
    )


def _simulate_incremental(
    requirements_by_material: dict[str, list[_Requirement]],
    candidates_by_material: dict[str, list[_Candidate]],
    selected_keys: frozenset[str],
    stock_map: dict[str, Decimal],
    lead_days_by_group: dict[str, int],
    baseline: _Scenario,
    affected_materials: set[str],
    jobs_by_order: dict[str, set[str]],
    order_by_job: dict[str, str],
) -> _Scenario:
    """Re-net only changed materials and merge their job/order deltas."""
    affected_requirements = [
        req
        for material_code in affected_materials
        for req in requirements_by_material.get(material_code, [])
    ]
    affected_candidates = [
        candidate
        for material_code in affected_materials
        for candidate in candidates_by_material.get(material_code, [])
    ]
    partial = _simulate(
        affected_requirements,
        affected_candidates,
        selected_keys,
        stock_map,
        lead_days_by_group,
    )

    affected_jobs = {req.works_order for req in affected_requirements}
    old_shortage_by_job: dict[str, Decimal] = defaultdict(Decimal)
    for req in affected_requirements:
        old_shortage_by_job[req.works_order] += baseline.requirement_shortage.get(
            req.row_id, Decimal(0)
        )

    job_shortage = baseline.job_shortage.copy()
    job_blockers = baseline.job_blockers.copy()
    ready_jobs = baseline.ready_jobs.copy()
    for job in affected_jobs:
        new_shortage = (
            baseline.job_shortage.get(job, Decimal(0))
            - old_shortage_by_job.get(job, Decimal(0))
            + partial.job_shortage.get(job, Decimal(0))
        )
        job_shortage[job] = new_shortage
        blockers = set(baseline.job_blockers.get(job, set())) - affected_materials
        blockers.update(partial.job_blockers.get(job, set()))
        job_blockers[job] = blockers
        if new_shortage == 0:
            ready_jobs.add(job)
        else:
            ready_jobs.discard(job)

    ready_orders = baseline.ready_orders.copy()
    affected_orders = {
        order_by_job[job] for job in affected_jobs if job in order_by_job
    }
    for so_number in affected_orders:
        if jobs_by_order[so_number].issubset(ready_jobs):
            ready_orders.add(so_number)
        else:
            ready_orders.discard(so_number)

    return _Scenario(
        requirement_shortage=partial.requirement_shortage,
        job_shortage=job_shortage,
        job_blockers=job_blockers,
        ready_jobs=ready_jobs,
        ready_orders=ready_orders,
    )


def _load_order_values(so_numbers: set[str]) -> dict[str, Decimal]:
    numeric_so_numbers = {int(number) for number in so_numbers if number.isdigit()}
    if not numeric_so_numbers:
        return {}

    rows = (
        SalesOrder.query
        .filter(SalesOrder.order_num.in_(numeric_so_numbers))
        .all()
    )
    release_values: dict[tuple[int, int, int], Decimal] = {}
    for row in rows:
        key = (row.order_num, row.order_line, row.rel_num)
        release_values.setdefault(key, row.release_price_gbp or Decimal(0))

    totals: dict[str, Decimal] = defaultdict(Decimal)
    for (order_num, _, _), value in release_values.items():
        totals[str(order_num)] += value
    return dict(totals)


def _potential_jobs(
    candidate: _Candidate,
    requirements: list[_Requirement],
    lead_days_by_group: dict[str, int],
) -> set[str]:
    jobs: set[str] = set()
    effective_date = max(candidate.due_date, date.today())
    for req in requirements:
        if req.material_code != candidate.material_code:
            continue
        lead_days = lead_days_by_group[req.group]
        deadline = req.due_date - timedelta(days=lead_days) if req.due_date else None
        if deadline is None or effective_date <= deadline:
            jobs.add(req.works_order)
    return jobs


def _impact_result(
    selected: tuple[_Candidate, ...],
    scenario: _Scenario,
    baseline: _Scenario,
    order_by_job: dict[str, str],
    order_values: dict[str, Decimal],
    potential_jobs: set[str],
) -> dict:
    blocked_potential_jobs = potential_jobs - baseline.ready_jobs
    jobs_unlocked = {
        job
        for job in blocked_potential_jobs
        if job in scenario.ready_jobs and job not in baseline.ready_jobs
    }
    improved_jobs = {
        job
        for job in blocked_potential_jobs
        if scenario.job_shortage.get(job, Decimal(0))
        < baseline.job_shortage.get(job, Decimal(0))
        and job not in jobs_unlocked
    }
    potential_orders = {
        order_by_job[job]
        for job in blocked_potential_jobs
        if job in order_by_job
    }
    orders_unlocked = {
        order
        for order in potential_orders
        if order in scenario.ready_orders and order not in baseline.ready_orders
    }
    detail_jobs = sorted(jobs_unlocked | improved_jobs)
    details = [
        {
            "works_order": job,
            "so_number": order_by_job.get(job),
            "before_shortage": baseline.job_shortage.get(job, Decimal(0)),
            "after_shortage": scenario.job_shortage.get(job, Decimal(0)),
            "blockers": sorted(scenario.job_blockers.get(job, set())),
            "unlocked": job in jobs_unlocked,
        }
        for job in detail_jobs
    ]
    cash_proxy = sum((po.cash_proxy for po in selected), Decimal(0))
    order_value_unlocked = sum(
        (order_values.get(order, Decimal(0)) for order in orders_unlocked),
        Decimal(0),
    )
    if jobs_unlocked:
        result = "Releases production"
        result_colour = "success"
    elif improved_jobs:
        result = "Partial release"
        result_colour = "warning"
    elif blocked_potential_jobs:
        result = "Blocked elsewhere"
        result_colour = "secondary"
    else:
        result = "No linked demand"
        result_colour = "secondary"

    return {
        "key": "+".join(po.key for po in selected),
        "pos": selected,
        "cash_proxy": cash_proxy,
        "affected_jobs": len(blocked_potential_jobs),
        "jobs_unlocked": sorted(jobs_unlocked),
        "jobs_improved": sorted(improved_jobs),
        "orders_unlocked": sorted(orders_unlocked),
        "order_value_unlocked": order_value_unlocked,
        "value_less_cash_proxy": order_value_unlocked - cash_proxy,
        "return_ratio": (
            order_value_unlocked / cash_proxy if cash_proxy > 0 else None
        ),
        "details": details,
        "result": result,
        "result_colour": result_colour,
    }


def _single_impact_sort_key(row: dict) -> tuple:
    """Rank releases by production impact, then value efficiency."""
    result_priority = {
        "Releases production": 0,
        "Partial release": 1,
        "Blocked elsewhere": 2,
        "No linked demand": 3,
    }
    return (
        result_priority.get(row["result"], 4),
        -len(row["jobs_unlocked"]),
        -len(row["orders_unlocked"]),
        -row["order_value_unlocked"],
        -len(row["jobs_improved"]),
        -(row["return_ratio"] or Decimal(0)),
        row["cash_proxy"],
        row["key"],
    )


def _bundle_impact_sort_key(row: dict) -> tuple:
    """Rank bundles by unique synergy before their wider production impact."""
    return (
        -len(row["synergy_jobs"]),
        *_single_impact_sort_key(row),
    )


def sort_release_impact_results(
    rows: list[dict],
    sort_by: str,
    bundled: bool = False,
) -> list[dict]:
    """Return a sorted copy using the selected financial or impact measure."""
    fallback = _bundle_impact_sort_key if bundled else _single_impact_sort_key
    if sort_by == "cash_proxy":
        key = lambda row: (row["cash_proxy"], fallback(row))
    elif sort_by == "orders_unlocked":
        key = lambda row: (-len(row["orders_unlocked"]), fallback(row))
    elif sort_by == "order_value_unlocked":
        key = lambda row: (-row["order_value_unlocked"], fallback(row))
    elif sort_by == "value_less_cash":
        key = lambda row: (-row["value_less_cash_proxy"], fallback(row))
    elif sort_by == "value_cash":
        key = lambda row: (
            row["return_ratio"] is None,
            -(row["return_ratio"] or Decimal(0)),
            fallback(row),
        )
    else:
        key = fallback
    return sorted(rows, key=key)


def _get_release_impact_uncached(
    max_bundle_size: int = 3,
    committed_keys: tuple[str, ...] = (),
    staged_keys: tuple[str, ...] = (),
    include_components: bool = True,
) -> dict:
    """Return single-release and synergistic bundle what-if results."""
    max_bundle_size = max(2, min(max_bundle_size, 3))
    requirements = _load_requirements(include_components=include_components)
    requirements_by_material: dict[str, list[_Requirement]] = defaultdict(list)
    descriptions_by_material: dict[str, str] = {}
    jobs_by_order: dict[str, set[str]] = defaultdict(set)
    order_by_job: dict[str, str] = {}
    for req in requirements:
        requirements_by_material[req.material_code].append(req)
        if req.description:
            descriptions_by_material.setdefault(req.material_code, req.description)
        if req.so_number:
            jobs_by_order[req.so_number].add(req.works_order)
            order_by_job[req.works_order] = req.so_number
    stock_map = _load_stock()
    lead_days_by_group = {
        group: _get_group_lead_days(group)
        for group in ("fabric", "component")
    }
    all_candidates = _load_candidates(
        {req.material_code for req in requirements},
        descriptions_by_material,
    )
    candidates_by_material: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in all_candidates:
        candidates_by_material[candidate.material_code].append(candidate)
    valid_keys = {candidate.key for candidate in all_candidates}
    committed_keys = set(committed_keys) & valid_keys
    staged_keys = (set(staged_keys) & valid_keys) - committed_keys
    committed_pos = [
        candidate for candidate in all_candidates if candidate.key in committed_keys
    ]
    initial_candidates = [
        candidate for candidate in all_candidates if candidate.key not in committed_keys
    ]
    baseline = _simulate(
        requirements,
        all_candidates,
        frozenset(committed_keys),
        stock_map,
        lead_days_by_group,
    )
    order_values = _load_order_values({
        req.so_number for req in requirements if req.so_number
    })

    potential_by_key = {
        candidate.key: _potential_jobs(
            candidate,
            requirements_by_material.get(candidate.material_code, []),
            lead_days_by_group,
        )
        for candidate in initial_candidates
    }
    blocked_jobs = set(baseline.job_shortage) - baseline.ready_jobs
    candidates = [
        candidate
        for candidate in initial_candidates
        if potential_by_key[candidate.key] & blocked_jobs
    ]
    single_results: list[dict] = []
    single_unlocked: dict[str, set[str]] = {}
    for candidate in candidates:
        scenario = _simulate_incremental(
            requirements_by_material,
            candidates_by_material,
            frozenset(committed_keys | {candidate.key}),
            stock_map,
            lead_days_by_group,
            baseline,
            {candidate.material_code},
            jobs_by_order,
            order_by_job,
        )
        result = _impact_result(
            (candidate,),
            scenario,
            baseline,
            order_by_job,
            order_values,
            potential_by_key[candidate.key],
        )
        single_results.append(result)
        single_unlocked[candidate.key] = set(result["jobs_unlocked"])

    single_results.sort(key=_single_impact_sort_key)

    staged_pos = tuple(
        candidate for candidate in candidates if candidate.key in staged_keys
    )
    staged_result = None
    if staged_pos:
        staged_materials = {po.material_code for po in staged_pos}
        staged_scenario = _simulate_incremental(
            requirements_by_material,
            candidates_by_material,
            frozenset(committed_keys | staged_keys),
            stock_map,
            lead_days_by_group,
            baseline,
            staged_materials,
            jobs_by_order,
            order_by_job,
        )
        staged_potential_jobs = set().union(
            *(potential_by_key[po.key] for po in staged_pos)
        )
        staged_result = _impact_result(
            staged_pos,
            staged_scenario,
            baseline,
            order_by_job,
            order_values,
            staged_potential_jobs,
        )
        staged_by_supplier: dict[str, list[_Candidate]] = defaultdict(list)
        for candidate in staged_pos:
            staged_by_supplier[candidate.supplier or "Unknown supplier"].append(
                candidate
            )
        staged_result["supplier_groups"] = [
            {
                "supplier": supplier,
                "pos": tuple(supplier_pos),
                "po_count": len(supplier_pos),
                "cash_proxy": sum(
                    (po.cash_proxy for po in supplier_pos), Decimal(0)
                ),
                "outstanding_qty": sum(
                    (po.outstanding_qty for po in supplier_pos), Decimal(0)
                ),
            }
            for supplier, supplier_pos in sorted(staged_by_supplier.items())
        ]

    candidates_by_supplier: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_supplier[candidate.supplier_key].append(candidate)

    supplier_results: list[dict] = []
    for supplier_candidates in candidates_by_supplier.values():
        selected = tuple(supplier_candidates)
        selected_keys = frozenset(po.key for po in selected)
        affected_materials = {po.material_code for po in selected}
        scenario = _simulate_incremental(
            requirements_by_material,
            candidates_by_material,
            frozenset(committed_keys | selected_keys),
            stock_map,
            lead_days_by_group,
            baseline,
            affected_materials,
            jobs_by_order,
            order_by_job,
        )
        potential_jobs = set().union(
            *(potential_by_key[po.key] for po in selected)
        )
        result = _impact_result(
            selected,
            scenario,
            baseline,
            order_by_job,
            order_values,
            potential_jobs,
        )
        result["supplier"] = selected[0].supplier or "Unknown supplier"
        result["po_count"] = len(selected)
        supplier_results.append(result)

    supplier_results.sort(key=_single_impact_sort_key)

    bundle_candidates = sorted(
        candidates,
        key=lambda po: (-len(potential_by_key[po.key]), po.cash_proxy, po.key),
    )[:_MAX_BUNDLE_CANDIDATES]
    bundle_results: list[dict] = []
    for size in range(2, max_bundle_size + 1):
        for selected in combinations(bundle_candidates, size):
            shared_jobs = set.intersection(
                *(potential_by_key[po.key] for po in selected)
            )
            if not shared_jobs:
                continue
            selected_keys = frozenset(po.key for po in selected)
            affected_materials = {po.material_code for po in selected}
            scenario = _simulate_incremental(
                requirements_by_material,
                candidates_by_material,
                frozenset(committed_keys | selected_keys),
                stock_map,
                lead_days_by_group,
                baseline,
                affected_materials,
                jobs_by_order,
                order_by_job,
            )
            potential_jobs = set().union(
                *(potential_by_key[po.key] for po in selected)
            )
            result = _impact_result(
                selected,
                scenario,
                baseline,
                order_by_job,
                order_values,
                potential_jobs,
            )
            individually_unlocked = set().union(
                *(single_unlocked[po.key] for po in selected)
            )
            synergy_jobs = set(result["jobs_unlocked"]) - individually_unlocked
            if not synergy_jobs:
                continue
            result["synergy_jobs"] = sorted(synergy_jobs)
            bundle_results.append(result)

    bundle_results.sort(key=_bundle_impact_sort_key)
    bundle_results = bundle_results[:_MAX_BUNDLE_RESULTS]

    linked_candidates = sum(1 for row in single_results if row["affected_jobs"])
    return {
        "single_results": single_results,
        "staged_result": staged_result,
        "staged_keys": sorted(po.key for po in staged_pos),
        "supplier_results": supplier_results,
        "bundle_results": bundle_results,
        "committed_pos": committed_pos,
        "committed_value": sum(
            (po.cash_proxy for po in committed_pos), Decimal(0)
        ),
        "total_candidate_count": len(all_candidates),
        "candidate_count": len(candidates),
        "linked_candidate_count": linked_candidates,
        "baseline_blocked_jobs": len(
            set(baseline.job_shortage) - baseline.ready_jobs
        ),
        "best_jobs_unlocked": max(
            (len(row["jobs_unlocked"]) for row in single_results),
            default=0,
        ),
        "has_requirements": bool(requirements),
        "include_components": include_components,
    }


@cache.memoize(timeout=300)
def _get_cached_release_impact(
    max_bundle_size: int,
    committed_keys: tuple[str, ...],
    staged_keys: tuple[str, ...],
    include_components: bool,
) -> dict:
    return _get_release_impact_uncached(
        max_bundle_size,
        committed_keys,
        staged_keys,
        include_components,
    )


def get_release_impact(
    max_bundle_size: int = 3,
    committed_keys: set[str] | None = None,
    staged_keys: set[str] | None = None,
    include_components: bool = True,
) -> dict:
    """Return cached single-release and synergistic bundle what-if results."""
    normalized_keys = tuple(sorted(committed_keys or set()))
    normalized_staged_keys = tuple(sorted(staged_keys or set()))
    if current_app.testing:
        return _get_release_impact_uncached(
            max_bundle_size,
            normalized_keys,
            normalized_staged_keys,
            include_components,
        )
    return _get_cached_release_impact(
        max_bundle_size,
        normalized_keys,
        normalized_staged_keys,
        include_components,
    )
