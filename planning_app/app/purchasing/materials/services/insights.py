"""
Shortage insight chart aggregations.

Derives chart/table data from already-netted ShortageRow lists — no DB queries.
"""
from __future__ import annotations

from decimal import Decimal

from .types import _MAT_STATUS_PRIORITY

__all__ = ["get_shortage_insights"]


def get_shortage_insights(rows: list, class_labels: dict[str, str] | None = None) -> dict:
    """
    Derive chart data from already-computed shortage rows.

    class_labels — optional {class_id: label} map (see services.class_labels) used to
    attach a human-readable "label" to each by_class entry for chart/legend display.

    Returns:
        {
            "top_materials":      [{"code", "description", "high_risk", "late_po",
                                    "med_risk", "low_risk", "at_risk_qty",
                                    "worst_status", "earliest_due"}, ...],  # top 10, one
                                   value per status tier so the chart can show all four
                                   tiers (including "Soft Risk") as separate stacked segments.
            "by_class":           [{"class_id", "label", "shortage_qty", "line_count"}, ...],
            "total_shortage_qty": Decimal,
            "unique_materials":   int,
            "status_counts":      {status: count, ...},
            "material_summary":   [{"material_code", "description", "class_id",
                                    "worst_status", "job_count", "total_shortage",
                                    "total_po_cover", "earliest_due"}, ...],
        }
    """
    class_labels = class_labels or {}
    at_risk_rows = [r for r in rows if r.status not in ("ok", "no_data")]
    short_rows   = [r for r in at_risk_rows if r.shortage > 0]

    # ---- Status breakdown (all tiers) ----
    status_counts: dict[str, int] = {}
    for r in at_risk_rows:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    # ---- Top 10 materials by total at-risk quantity, broken down per status tier ----
    # "at_risk_qty" = net requirement not covered by owned stock. Split per-tier (rather
    # than collapsing med_risk/low_risk into a single "PO-reliant" bucket) so the chart
    # can show all four at-risk tiers — including "Soft Risk" (low_risk) — distinctly,
    # matching the KPI filter cards above it.
    _TIERS = ("high_risk", "late_po", "med_risk", "low_risk")
    mat_totals: dict[str, dict] = {}
    for r in at_risk_rows:
        mc = r.material_code
        if mc not in mat_totals:
            mat_totals[mc] = {
                "code":         mc,
                "description":  r.description,
                "high_risk":    Decimal(0),
                "late_po":      Decimal(0),
                "med_risk":     Decimal(0),
                "low_risk":     Decimal(0),
                "worst_status": r.status,
                "earliest_due": r.due_date,
            }
        m = mat_totals[mc]
        at_risk_qty = max(Decimal(0), r.net_required - r.stock_on_hand)
        if r.status in _TIERS:
            m[r.status] += at_risk_qty
        if _MAT_STATUS_PRIORITY.get(r.status, 0) > _MAT_STATUS_PRIORITY.get(m["worst_status"], 0):
            m["worst_status"] = r.status
        if r.due_date and (m["earliest_due"] is None or r.due_date < m["earliest_due"]):
            m["earliest_due"] = r.due_date

    for m in mat_totals.values():
        m["at_risk_qty"] = m["high_risk"] + m["late_po"] + m["med_risk"] + m["low_risk"]

    top_materials = sorted(mat_totals.values(), key=lambda x: x["at_risk_qty"], reverse=True)[:10]

    # ---- At-risk lines by material class (also uses total at-risk qty, all tiers) ----
    class_totals: dict[str, dict] = {}
    for r in at_risk_rows:
        cid = r.class_id or "Unknown"
        if cid not in class_totals:
            class_totals[cid] = {
                "class_id": cid,
                "label": class_labels.get(cid, cid),
                "shortage_qty": Decimal(0),
                "line_count": 0,
            }
        class_totals[cid]["shortage_qty"] += max(Decimal(0), r.net_required - r.stock_on_hand)
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
                "total_at_risk":  Decimal(0),
                "total_po_cover": Decimal(0),
                "earliest_due":   r.due_date,
            }
        m = mat_summary[mc]
        if _MAT_STATUS_PRIORITY.get(r.status, 0) > _MAT_STATUS_PRIORITY.get(m["worst_status"], 0):
            m["worst_status"] = r.status
        if r.works_order:
            m["jobs"].add(r.works_order)
        m["total_shortage"] += r.shortage
        m["total_at_risk"]  += max(Decimal(0), r.net_required - r.stock_on_hand)
        m["total_po_cover"] += r.po_coverage or Decimal(0)
        if r.due_date and (m["earliest_due"] is None or r.due_date < m["earliest_due"]):
            m["earliest_due"] = r.due_date

    material_summary = []
    for m in mat_summary.values():
        m["job_count"] = len(m["jobs"])
        del m["jobs"]
        material_summary.append(m)
    material_summary.sort(
        key=lambda m: (-_MAT_STATUS_PRIORITY.get(m["worst_status"], 0), -m["total_at_risk"])
    )

    return {
        "top_materials":      top_materials,
        "by_class":           by_class,
        "total_shortage_qty": sum(r.shortage for r in short_rows),
        "unique_materials":   len(mat_totals),
        "status_counts":      status_counts,
        "material_summary":   material_summary,
    }
