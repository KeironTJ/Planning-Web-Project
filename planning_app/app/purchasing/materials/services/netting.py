"""
MRP netting engine.

Shortage calculation (cumulative MRP netting):
  Requirements are sorted by due_date per material. Stock, CO and PO are treated
  as shared pools consumed progressively by earlier requirements before later ones
  are assessed. This gives a true picture of material availability rather than
  showing the same total stock on every row for the same material.

  Consumption order per requirement: stock → CO (call-off) → actual PO

Where:
  net_requirement (main)        = qty_for_order - qty_issued
  CO orders                     = outstanding_qty on POs with po_type == "CO"
                                  (finite pool, consumed in date order like stock)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from app.extensions import db
from ..models import MaterialRequirementMain
from .loaders import (
    _get_group_class_ids,
    _get_group_lead_days,
    _load_co_qty,
    _load_exempt_codes,
    _load_po_coverage,
    _load_stock,
)
from .types import ShortageRow, _MAT_STATUS_PRIORITY

__all__ = [
    "get_shortage_report",
    "_row_status",
    "_has_reqs",
]


def _row_status(
    net_required: Decimal,
    shortage: Decimal,
    stock_on_hand: Decimal,
    released: Optional[bool],
    po_exists: bool,
) -> str:
    """Single source of truth for the 5-tier material coverage status."""
    if net_required == Decimal(0):
        return "ok"
    if shortage > Decimal(0):
        return "late_po" if po_exists else "high_risk"
    if stock_on_hand < net_required:
        return "med_risk" if released else "low_risk"
    return "ok"


def _has_reqs(group: str = "fabric") -> bool:
    return bool(
        db.session.query(func.count(MaterialRequirementMain.id))
        .filter(MaterialRequirementMain.material_group == group)
        .scalar()
    )


def get_shortage_report(
    source: str = "all",            # retained for URL compat; ignored in netting logic
    material_group: str = "fabric", # "fabric" | "component"
    dept_filter: Optional[str] = None,
    search: Optional[str] = None,
    so_filter: Optional[str] = None,  # exact SO number to scope results to one order
    shortages_only: bool = True,
    due_before: Optional[date] = None,
    due_from: Optional[date] = None,
) -> dict:
    """
    Compute material shortages using cumulative MRP netting.

    material_group selects which slice of material_requirements to assess:
      "fabric"    — rows imported from PlanningMatReq (fabrics/hides)
      "component" — rows imported from PlanningMatReqComp (all components)

    Netting logic:
      1. Collect ALL requirements for the group (no display filters yet).
      2. Group by material_code, sort each group by due_date.
         Process cumulatively: stock, CO and PO are shared pools consumed in
         date order, so later requirements only see what earlier ones left behind.
      3. Apply display filters to the already-netted rows before returning.

    Returns:
        {
            "rows": [ShortageRow, ...],
            "total_rows": int,
            "shortage_count": int,
            "stock_imported": bool,
            "reqs_imported": bool,
        }
    """
    lead_days    = _get_group_lead_days(material_group)
    class_ids    = _get_group_class_ids(material_group)
    stock_map    = _load_stock()
    po_map       = _load_po_coverage()   # actual POs; overdue dates clamped to today
    co_qty_map   = _load_co_qty()        # CO call-offs, treated as finite pool
    exempt_codes = _load_exempt_codes()

    # ---- Phase 1: collect ALL raw requirements for this group ----
    raw: list[dict] = []

    _base_q = MaterialRequirementMain.query.filter(
        MaterialRequirementMain.material_group == material_group,
        MaterialRequirementMain.job_closed != True,
        MaterialRequirementMain.issued_complete != True,
    )
    if class_ids:
        _base_q = _base_q.filter(MaterialRequirementMain.class_id.in_(class_ids))
    # so_filter is intentionally NOT applied here — netting must use the full stock pool
    # so that cumulative consumption matches the per-SO status shown on WIP/order book badges.

    for req in _base_q.order_by(MaterialRequirementMain.due_date).all():
        mc = req.material_code or ""
        qty_req    = req.qty_for_order or Decimal(0)
        qty_issued = req.qty_issued    or Decimal(0)
        net_req    = max(Decimal(0), qty_req - qty_issued)
        raw.append({
            "source":        "main",
            "material_code": mc,
            "description":   req.material_description or "",
            "department":    req.warehouse_code or "",
            "due_date":      req.due_date,
            "qty_required":  qty_req,
            "qty_issued":    qty_issued,
            "net_required":  net_req,
            "works_order":   req.works_order,
            "so_number":     req.so_number,
            "customer_id":   None,
            "customer":      None,
            "complete":      "Y" if (req.job_closed or req.issued_complete) else "",
            "class_id":      req.class_id or "",
            "job_released":  req.job_released,
            "_search_text":  f"{mc} {req.material_description or ''} {req.works_order or ''}".lower(),
        })

    # ---- Phase 2: cumulative netting per material ----
    # Drop exempt materials entirely — they have no POs by design.
    by_material: dict[str, list[dict]] = defaultdict(list)
    for r in raw:
        if r["material_code"] not in exempt_codes:
            by_material[r["material_code"]].append(r)

    all_netted: list[dict] = []

    for mc, reqs in by_material.items():
        reqs.sort(key=lambda r: (r["due_date"] or date.max, (r.get("works_order") or "").strip()))

        remaining_stock = stock_map.get(mc, Decimal(0))
        remaining_co    = co_qty_map.get(mc, Decimal(0))
        po_lines        = sorted(po_map.get(mc, []), key=lambda x: x[0])
        po_consumed     = Decimal(0)
        po_total_qty    = sum(qty for _, qty in po_lines)

        for req in reqs:
            net_req = req["net_required"]
            req_due = req["due_date"]

            # PO must arrive at least lead_days before the requirement due date.
            po_deadline = (req_due - timedelta(days=lead_days)) if req_due else None

            po_gross = sum(
                (qty for d, qty in po_lines if po_deadline is None or d <= po_deadline),
                Decimal(0),
            )
            po_avail = max(Decimal(0), po_gross - po_consumed)

            stock_before = remaining_stock
            co_before    = remaining_co

            shortage = max(Decimal(0), net_req - remaining_stock - remaining_co - po_avail)

            to_cover   = min(net_req, remaining_stock + remaining_co + po_avail)
            stock_used = min(remaining_stock, to_cover)
            co_used    = min(remaining_co, to_cover - stock_used)
            po_used    = min(po_avail,     to_cover - stock_used - co_used)

            remaining_stock  = max(Decimal(0), remaining_stock - stock_used)
            remaining_co     = max(Decimal(0), remaining_co    - co_used)
            po_consumed     += po_used

            req["_stock_on_hand"] = stock_before
            req["_po_coverage"]   = po_avail + co_before
            req["_shortage"]      = shortage
            req["_po_exists"]     = po_avail > 0 or (po_total_qty - po_consumed) > 0
            all_netted.append(req)

    # ---- Phase 3: apply display filters and build ShortageRows ----
    search_term = search.strip().lower() if search else None

    rows: list[ShortageRow] = []
    for r in all_netted:
        if so_filter and r.get("so_number") != str(so_filter):
            continue
        if dept_filter and r["department"] != dept_filter:
            continue
        if due_from and r["due_date"] and r["due_date"] < due_from:
            continue
        if due_before and r["due_date"] and r["due_date"] > due_before:
            continue
        if search_term and search_term not in r["_search_text"]:
            continue
        if shortages_only and r["_shortage"] == 0:
            continue

        rows.append(ShortageRow(
            source=r["source"],
            material_code=r["material_code"],
            description=r["description"],
            department=r["department"],
            due_date=r["due_date"],
            qty_required=r["qty_required"],
            qty_issued=r["qty_issued"],
            net_required=r["net_required"],
            stock_on_hand=r["_stock_on_hand"],
            po_coverage=r["_po_coverage"],
            shortage=r["_shortage"],
            works_order=r["works_order"],
            so_number=r.get("so_number"),
            customer_id=r["customer_id"],
            customer=r["customer"],
            complete=r["complete"],
            class_id=r.get("class_id") or None,
            job_released=r.get("job_released"),
            po_exists=r.get("_po_exists", False),
            status=_row_status(
                r["net_required"], r["_shortage"], r["_stock_on_hand"],
                r.get("job_released"), r.get("_po_exists", False),
            ),
        ))

    rows.sort(key=lambda r: (r.due_date or date.max, -r.shortage))

    shortage_count = sum(1 for r in rows if r.shortage > 0)

    return {
        "rows":           rows,
        "total_rows":     len(rows),
        "shortage_count": shortage_count,
        "stock_imported": bool(stock_map),
        "reqs_imported":  bool(raw) or _has_reqs(material_group),
    }
