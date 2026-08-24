"""
SO and job material/component status lookups.

All four public functions share the same netting result (via the request-level
cache in netting.py) and differ only in which material group and which row
attribute they key on.
"""
from __future__ import annotations

from typing import Optional

from ._cache import _cached_group_report, _cached_unfiltered_report
from .netting import _row_status
from .types import _MAT_STATUS_PRIORITY

__all__ = [
    "get_so_material_status",
    "get_job_material_status",
    "get_so_component_status",
    "get_job_component_status",
]


def _compute_status_map(report: dict, key_attr: str, keys: set[str]) -> dict[str, str]:
    """
    Walk netted rows and return the worst-case status per key.

    key_attr — ShortageRow attribute to group by ("so_number" or "works_order")
    keys     — set of values to include in the result
    """
    result: dict[str, str] = {k: "no_data" for k in keys}
    for row in report["rows"]:
        key = getattr(row, key_attr)
        if not key or key not in keys:
            continue
        line_status = _row_status(
            row.net_required, row.shortage, row.stock_on_hand, row.job_released, row.po_exists,
        )
        if _MAT_STATUS_PRIORITY.get(line_status, 0) > _MAT_STATUS_PRIORITY.get(result[key], -1):
            result[key] = line_status
    return result


def get_so_material_status(
    so_numbers: list[str],
    plan_start_map: Optional[dict] = None,  # accepted for API compatibility; not applied
) -> dict[str, str]:
    """
    Compute material availability status for a list of SO numbers (fabric group).

    Uses the same cumulative MRP netting as get_shortage_report() so that shared
    materials are correctly allocated in due-date order.

    Returns {so_number: status} with tier codes:
        ok / low_risk / med_risk / late_po / high_risk / no_data
    """
    if not so_numbers:
        return {}
    report = _cached_unfiltered_report()
    return _compute_status_map(report, "so_number", set(so_numbers))


def get_job_material_status(job_nums: list[str]) -> dict[str, str]:
    """
    Compute material availability status per production job number (fabric group).

    Returns {job_num: status} with the same tier codes.
    """
    if not job_nums:
        return {}
    report = _cached_unfiltered_report()
    return _compute_status_map(report, "works_order", set(job_nums))


def get_so_component_status(so_numbers: list[str]) -> dict[str, str]:
    """
    Compute component availability status per SO number (component group).

    Returns {so_number: status} with the same tier codes.
    """
    if not so_numbers:
        return {}
    report = _cached_group_report("component")
    return _compute_status_map(report, "so_number", set(so_numbers))


def get_job_component_status(job_nums: list[str]) -> dict[str, str]:
    """
    Compute component availability status per job number (component group).

    Returns {job_num: status} with the same tier codes.
    """
    if not job_nums:
        return {}
    report = _cached_group_report("component")
    return _compute_status_map(report, "works_order", set(job_nums))
