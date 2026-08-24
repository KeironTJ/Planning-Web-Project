"""
Shortage insight chart aggregations.

Derives chart/table data from already-netted ShortageRow lists — no DB queries.
"""
from __future__ import annotations

from decimal import Decimal

from .types import _MAT_STATUS_PRIORITY

__all__ = ["get_shortage_insights"]


def get_shortage_insights(rows: list) -> dict:
    """
    Derive chart data from already-computed shortage rows.

    Returns:
        {
            "top_materials":      [{"code", "description", "shortage", "earliest_due"}, ...],  # top 10
            "by_class":           [{"class_id", "shortage_qty", "line_count"}, ...],
            "total_shortage_qty": Decimal,
            "unique_materials":   int,
            "status_counts":      {status: count, ...},
            "material_summary":   [{"material_code", "description", "class_id",
                                    "worst_status", "job_count", "total_shortage",
                                    "total_po_cover", "earliest_due"}, ...],
        }
    """
    at_risk_rows = [r for r in rows if r.status not in ("ok", "no_data")]
    short_rows   = [r for r in at_risk_rows if r.shortage > 0]

    # ---- Status breakdown (all tiers) ----
    status_counts: dict[str, int] = {}
    for r in at_risk_rows:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    # ---- Top 10 materials by total shortage quantity ----
    mat_totals: dict[str, dict] = {}
    for r in at_risk_rows:
        mc = r.material_code
        if mc not in mat_totals:
            mat_totals[mc] = {
                "code":        mc,
                "description": r.description,
                "shortage":    Decimal(0),
                "earliest_due": r.due_date,
            }
        mat_totals[mc]["shortage"] += r.shortage
        if r.due_date and (
            mat_totals[mc]["earliest_due"] is None
            or r.due_date < mat_totals[mc]["earliest_due"]
        ):
            mat_totals[mc]["earliest_due"] = r.due_date

    top_materials = sorted(mat_totals.values(), key=lambda x: x["shortage"], reverse=True)[:10]

    # ---- At-risk lines by material class ----
    class_totals: dict[str, dict] = {}
    for r in at_risk_rows:
        cid = r.class_id or "Unknown"
        if cid not in class_totals:
            class_totals[cid] = {"class_id": cid, "shortage_qty": Decimal(0), "line_count": 0}
        class_totals[cid]["shortage_qty"] += r.shortage
        class_totals[cid]["line_count"]   += 1

    by_class = sorted(class_totals.values(), key=lambda x: x["shortage_qty"], reverse=True)

    # ---- Per-material summary (all at-risk statuses) ----
    mat_summary: dict[str, dict] = {}
    for r in at_risk_rows:
        mc = r.material_code
        if mc not in mat_summary:
            mat_summary[mc] = {
                "material_code":  mc,
                "description":    r.description,
                "class_id":       r.class_id,
                "worst_status":   r.status,
                "jobs":           set(),
                "total_shortage": Decimal(0),
                "total_po_cover": Decimal(0),
                "earliest_due":   r.due_date,
            }
        m = mat_summary[mc]
        if _MAT_STATUS_PRIORITY.get(r.status, 0) > _MAT_STATUS_PRIORITY.get(m["worst_status"], 0):
            m["worst_status"] = r.status
        if r.works_order:
            m["jobs"].add(r.works_order)
        m["total_shortage"] += r.shortage
        m["total_po_cover"] += r.po_coverage or Decimal(0)
        if r.due_date and (m["earliest_due"] is None or r.due_date < m["earliest_due"]):
            m["earliest_due"] = r.due_date

    material_summary = []
    for m in mat_summary.values():
        m["job_count"] = len(m["jobs"])
        del m["jobs"]
        material_summary.append(m)
    material_summary.sort(
        key=lambda m: (-_MAT_STATUS_PRIORITY.get(m["worst_status"], 0), -m["total_shortage"])
    )

    return {
        "top_materials":      top_materials,
        "by_class":           by_class,
        "total_shortage_qty": sum(r.shortage for r in short_rows),
        "unique_materials":   len(mat_totals),
        "status_counts":      status_counts,
        "material_summary":   material_summary,
    }
