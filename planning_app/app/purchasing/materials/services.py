"""
Materials service layer.

Shortage calculation (cumulative MRP netting):
  Requirements are sorted by due_date per material. Stock, CO and PO are treated
  as shared pools that are consumed progressively by earlier requirements before
  later ones are assessed. This gives a true picture of material availability
  rather than showing the same total stock on every row for the same material.

  Consumption order per requirement: stock → CO (call-off) → actual PO

Where:
  net_requirement (main)        = qty_for_order - qty_issued
  net_requirement (after sales) = qty_required
  CO orders                     = outstanding_qty on POs with po_type == "CO"
                                  (finite pool, consumed in date order like stock)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from app.extensions import db
from .models import (
    Stock,
    PurchaseOrder,
    MaterialRequirementMain,
    MrpExemptMaterial,
)


def _cached_group_report(group: str) -> dict:
    """Return get_shortage_report(material_group=group, shortages_only=False), cached per request."""
    cache_key = f"_mrp_{group}_cache"
    try:
        from flask import g
        if not hasattr(g, cache_key):
            setattr(g, cache_key, get_shortage_report(material_group=group, shortages_only=False))
        return getattr(g, cache_key)
    except RuntimeError:
        return get_shortage_report(material_group=group, shortages_only=False)


def _cached_unfiltered_report() -> dict:
    """
    Return the fabric-group shortage report for this request, cached on flask.g.

    Multiple callers on the same page load (get_stock_summary, get_weekly_availability_summary,
    get_so_material_status) share the single computation instead of each running a
    full MRP netting pass independently.
    """
    return _cached_group_report("fabric")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ShortageRow:
    source: str              # "main" | "po" | "co" | ""
    material_code: str
    description: str
    department: str
    due_date: Optional[date]
    qty_required: Decimal
    qty_issued: Decimal
    net_required: Decimal
    stock_on_hand: Decimal
    po_coverage: Decimal
    shortage: Decimal
    # Source record identifiers
    works_order: Optional[str] = None
    so_number: Optional[str] = None
    customer_id: Optional[str] = None
    customer: Optional[str] = None
    complete: Optional[str] = None
    class_id: Optional[str] = None
    job_released: Optional[bool] = None
    po_exists: bool = False  # any open PO for this material exists (may be overdue/insufficient)
    status: str = "no_data"  # 5-tier coverage status — set during netting


@dataclass
class MrpEvent:
    event_date: Optional[date]
    row_type: str          # "opening" | "co" | "po" | "requirement"
    reference: str
    source: str            # "main" | "po" | "co" | ""
    department: str
    demand: Optional[Decimal]
    receipt: Optional[Decimal]
    balance: Decimal
    is_short: bool = False
    job_released: Optional[bool] = None  # None for non-requirement rows
    job_firm: Optional[bool] = None      # None for non-requirement rows
    mat_status: Optional[str] = None     # 5-tier coverage status for requirement rows


@dataclass
class MrpMaterial:
    material_code: str
    description: str
    opening_stock: Decimal
    has_shortage: bool
    events: list
    mat_status: str = "no_data"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_exempt_codes() -> frozenset[str]:
    """Return the set of material codes exempt from MRP shortage calculations."""
    rows = db.session.query(MrpExemptMaterial.material_code).all()
    return frozenset(r.material_code for r in rows)


def _load_stock() -> dict[str, Decimal]:
    """
    Return {part_num: total_qty_on_hand} across all plants.

    The PlanningStockReportComp BAQ returns one row per (part_num, plant).
    Summing ensures multi-plant parts (e.g. fabric stored in STORES + PROD)
    are not understated when only the last plant row would otherwise be kept.
    """
    rows = (
        db.session.query(
            Stock.part_num,
            func.sum(Stock.qty_on_hand).label("total_qty"),
        )
        .group_by(Stock.part_num)
        .all()
    )
    return {r.part_num: (r.total_qty or Decimal(0)) for r in rows}


def _load_po_coverage() -> dict[str, list[tuple[date, Decimal]]]:
    """
    Return {part_num: [(effective_date, outstanding_qty), ...]} for all open PO releases.

    Overdue POs (due_date < today) are clamped to today — they are still
    outstanding (not yet received) but assumed to arrive at the earliest now.
    Only rows with a due_date are included.
    """
    today = date.today()
    rows = (
        db.session.query(
            PurchaseOrder.part_num,
            PurchaseOrder.due_date,
            PurchaseOrder.outstanding_qty,
        )
        .filter(
            PurchaseOrder.part_num.isnot(None),
            PurchaseOrder.outstanding_qty > 0,
            PurchaseOrder.due_date.isnot(None),
        )
        .order_by(PurchaseOrder.part_num, PurchaseOrder.due_date)
        .all()
    )
    coverage: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for r in rows:
        effective = r.due_date if r.due_date >= today else today
        coverage[r.part_num].append((effective, r.outstanding_qty or Decimal(0)))
    return dict(coverage)


def _get_lead_days() -> int:
    """Return fabric-group MRP lead days from SystemSetting (default 14)."""
    from app.admin.models import SystemSetting, SETTING_MRP_LEAD_DAYS
    return SystemSetting.get_int(SETTING_MRP_LEAD_DAYS, default=14)


def _get_group_lead_days(group: str) -> int:
    """Return the configured MRP lead days for the given material group."""
    from app.admin.models import SystemSetting, SETTING_MRP_LEAD_DAYS, SETTING_MRP_COMPONENT_LEAD_DAYS
    key = SETTING_MRP_LEAD_DAYS if group == "fabric" else SETTING_MRP_COMPONENT_LEAD_DAYS
    return SystemSetting.get_int(key, default=14)


def _get_group_class_ids(group: str) -> frozenset[str] | None:
    """
    Return the configured class-ID filter for the given material group.

    Returns None when no filter is set (all classes in the group are included).
    The fabric group defaults to the legacy class list; the component group
    defaults to empty (= no filter, include all classes in PlanningMatReqComp).
    """
    from app.admin.models import SystemSetting, SETTING_FABRIC_CLASS_IDS, SETTING_COMPONENT_CLASS_IDS
    if group == "fabric":
        raw = SystemSetting.get(SETTING_FABRIC_CLASS_IDS, "A101,A102,A105,B101,C101,Z102")
    else:
        raw = SystemSetting.get(SETTING_COMPONENT_CLASS_IDS, "")
    raw = raw.strip()
    if not raw:
        return None
    return frozenset(c.strip() for c in raw.split(",") if c.strip())


def _load_co_qty() -> dict[str, Decimal]:
    """
    Call-off (CO) order pool — not available from the OSPurchaseOrders BAQ.
    Returns an empty dict; CO coverage is not currently split from standard POs.
    """
    # NOTE: The OSPurchaseOrders BAQ does not expose a PO type flag.
    # All POs are treated as date-constrained for now.
    co_qty: dict[str, Decimal] = {}
    for r in []:
        co_qty[r.product_code] += (r.outstanding_qty or Decimal(0))
    return dict(co_qty)


# ---------------------------------------------------------------------------
# Shortage calculation
# ---------------------------------------------------------------------------

def get_shortage_report(
    source: str = "all",           # retained for URL compat; ignored in netting logic
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

    Each group has its own lead-days and class-ID filter configured via
    admin settings (fabric_class_ids, component_class_ids,
    mrp_material_lead_days, mrp_component_lead_days).

    Netting logic:
      1. Collect ALL requirements for the group (no display filters yet).
      2. Group by material_code, sort each group by due_date.
         Process cumulatively: stock, CO and PO are shared pools consumed in
         date order, so later requirements only see what earlier ones left behind.
      3. Apply display filters (dept, search, due_before, shortages_only) to
         the already-netted rows before returning them.

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
    exempt_codes = _load_exempt_codes()  # materials excluded from shortage reporting

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
            "source":       "main",
            "material_code": mc,
            "description":  req.material_description or "",
            "department":   req.warehouse_code or "",  # dept not in BAQ; warehouse as proxy
            "due_date":     req.due_date,
            "qty_required": qty_req,
            "qty_issued":   qty_issued,
            "net_required": net_req,
            "works_order":  req.works_order,
            "so_number":    req.so_number,
            "customer_id":  None,  # not available from BAQ
            "customer":     None,
            "complete":     "Y" if (req.job_closed or req.issued_complete) else "",
            "class_id":     req.class_id or "",
            "job_released": req.job_released,
            "_search_text": f"{mc} {req.material_description or ''} {req.works_order or ''}".lower(),
        })

    # ---- Phase 2: cumulative netting per material ----
    # Drop exempt materials entirely — they have no POs by design, so reporting
    # them as shortages would be misleading.
    by_material: dict[str, list[dict]] = defaultdict(list)
    for r in raw:
        if r["material_code"] not in exempt_codes:
            by_material[r["material_code"]].append(r)

    all_netted: list[dict] = []

    for mc, reqs in by_material.items():
        from datetime import timedelta as _td
        # Sort by due_date (None → treated as very far future)
        reqs.sort(key=lambda r: (r["due_date"] or date.max))

        remaining_stock = stock_map.get(mc, Decimal(0))
        remaining_co    = co_qty_map.get(mc, Decimal(0))
        po_lines        = sorted(po_map.get(mc, []), key=lambda x: x[0])  # (effective_date, qty)
        po_consumed     = Decimal(0)
        po_total_qty    = sum(qty for _, qty in po_lines)  # fixed; used to detect exhaustion

        for req in reqs:
            net_req = req["net_required"]
            req_due = req["due_date"]

            # PO must arrive at least lead_days before the requirement due date.
            # _load_po_coverage() has already clamped overdue POs to today.
            po_deadline = (req_due - _td(days=lead_days)) if req_due else None

            # PO quantity available up to the lead-time deadline,
            # minus what earlier requirements in this material group already consumed
            po_gross = sum(
                (qty for d, qty in po_lines if po_deadline is None or d <= po_deadline),
                Decimal(0),
            )
            po_avail = max(Decimal(0), po_gross - po_consumed)

            # Record what was available *before* this req consumes anything
            stock_before = remaining_stock
            co_before    = remaining_co

            # Compute shortage: what's left after stock + CO + PO
            shortage = max(Decimal(0), net_req - remaining_stock - remaining_co - po_avail)

            # Consume pools in order: stock → CO → PO
            to_cover   = min(net_req, remaining_stock + remaining_co + po_avail)
            stock_used = min(remaining_stock, to_cover)
            co_used    = min(remaining_co, to_cover - stock_used)
            po_used    = min(po_avail,     to_cover - stock_used - co_used)

            remaining_stock  = max(Decimal(0), remaining_stock - stock_used)
            remaining_co     = max(Decimal(0), remaining_co    - co_used)
            po_consumed     += po_used

            req["_stock_on_hand"] = stock_before
            req["_po_coverage"]   = po_avail + co_before   # CO + PO available at this point
            req["_shortage"]      = shortage
            # po_exists: True if a PO could still help (available for this req, or remaining
            # unconsumed quantity exists). False when the PO pool is fully exhausted.
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

    # Sort by due_date, then worst shortage first
    rows.sort(key=lambda r: (r.due_date or date.max, -r.shortage))

    shortage_count = sum(1 for r in rows if r.shortage > 0)

    return {
        "rows":           rows,
        "total_rows":     len(rows),
        "shortage_count": shortage_count,
        "stock_imported": bool(stock_map),
        "reqs_imported":  bool(raw) or _has_reqs(material_group),
    }


# ---------------------------------------------------------------------------
# Per-SO material status (for WIP tracker integration)
# ---------------------------------------------------------------------------

#: Priority for worst-case rollup — higher number = worse status
_MAT_STATUS_PRIORITY: dict[str, int] = {
    "no_data":   -1,
    "ok":         0,
    "low_risk":   1,
    "med_risk":   2,
    "late_po":    3,
    "high_risk":  4,
}

#: Display metadata: status -> (label, Bootstrap colour)
MAT_STATUS_META: dict[str, tuple[str, str]] = {
    "ok":        ("Mat. OK",       "success"),
    "low_risk":  ("Soft Risk",    "info"),     # gap exists but job not yet released
    "med_risk":  ("PO Reliant",   "warning"),  # gap covered by PO, job released
    "late_po":   ("Late PO",       "orange"),   # genuine shortage but a PO exists
    "high_risk": ("Shortage",      "danger"),   # genuine shortage, no PO exists
    "no_data":   ("\u2014",         "secondary"),
}


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
        # Genuine shortage — escalate regardless of release status.
        return "late_po" if po_exists else "high_risk"
    if stock_on_hand < net_required:
        # Gap covered by PO: urgency depends on whether job is in production.
        return "med_risk" if released else "low_risk"
    return "ok"


def get_so_material_status(
    so_numbers: list[str],
    plan_start_map: Optional[dict] = None,
) -> dict[str, str]:
    """
    Compute material availability status for a list of SO numbers.

    Uses the same cumulative MRP netting as get_shortage_report() so that shared
    materials (fabric, hide, etc.) are correctly allocated in due-date order.
    The previous per-SO independent assessment was incorrect: it showed the full
    stock pool as available to every SO simultaneously, so shortages only appeared
    after total demand exceeded total supply — not when individual SOs ran out.

    plan_start_map: accepted for API compatibility; not currently applied in the
        cumulative netting path (the ERP due_date is used directly).

    Coverage tiers (worst-case across all requirement lines for the SO):
        ok        — net requirement fully covered by stock on hand
        low_risk  — remaining covered by Call-Off orders (Type=CO, open-ended)
        med_risk  — remaining covered by actual POs (Type=PO, due <= effective date)
        high_risk — still uncovered after stock + CO + PO
        no_data   — no MRP requirements found for this SO
    """
    if not so_numbers:
        return {}

    so_set = set(so_numbers)
    result: dict[str, str] = {so: "no_data" for so in so_numbers}

    # Use request-level cache so the netting is shared with other callers on
    # the same page load (get_stock_summary, get_weekly_availability_summary).
    report = _cached_unfiltered_report()

    for row in report["rows"]:
        so = row.so_number
        if not so or so not in so_set:
            continue

        line_status = _row_status(
            row.net_required, row.shortage, row.stock_on_hand, row.job_released, row.po_exists,
        )
        if _MAT_STATUS_PRIORITY.get(line_status, 0) > _MAT_STATUS_PRIORITY.get(result[so], -1):
            result[so] = line_status

    return result


def get_job_material_status(job_nums: list[str]) -> dict[str, str]:
    """
    Compute material availability status per production job number.

    Uses the same cached MRP netting as get_so_material_status() so there is no
    extra database cost when both are called on the same request.

    Returns {job_num: status} with the same tier codes:
        ok / low_risk / med_risk / high_risk / no_data
    """
    if not job_nums:
        return {}

    job_set = set(job_nums)
    result: dict[str, str] = {j: "no_data" for j in job_nums}

    report = _cached_unfiltered_report()

    for row in report["rows"]:
        job = row.works_order
        if not job or job not in job_set:
            continue

        line_status = _row_status(
            row.net_required, row.shortage, row.stock_on_hand, row.job_released, row.po_exists,
        )
        if _MAT_STATUS_PRIORITY.get(line_status, 0) > _MAT_STATUS_PRIORITY.get(result[job], -1):
            result[job] = line_status

    return result


# ---------------------------------------------------------------------------
# Component availability status (parallel to fabric, using component group)
# ---------------------------------------------------------------------------

def get_so_component_status(so_numbers: list[str]) -> dict[str, str]:
    """
    Compute component availability status per SO number using the 'component'
    material group (sourced from PlanningMatReqComp).

    Uses the same 5-tier status scheme as get_so_material_status(), but
    draws on a separate MRP netting pass over the component group.
    Class-ID filtering is applied per the 'component_class_ids' system setting.

    Returns {so_number: status} with the same tier codes:
        ok / low_risk / med_risk / late_po / high_risk / no_data
    """
    if not so_numbers:
        return {}

    so_set = set(so_numbers)
    result: dict[str, str] = {so: "no_data" for so in so_numbers}
    report = _cached_group_report("component")

    for row in report["rows"]:
        so = row.so_number
        if not so or so not in so_set:
            continue
        line_status = _row_status(
            row.net_required, row.shortage, row.stock_on_hand, row.job_released, row.po_exists,
        )
        if _MAT_STATUS_PRIORITY.get(line_status, 0) > _MAT_STATUS_PRIORITY.get(result[so], -1):
            result[so] = line_status

    return result


def get_job_component_status(job_nums: list[str]) -> dict[str, str]:
    """
    Compute component availability status per job number using the 'component' group.

    Returns {job_num: status} with the same tier codes:
        ok / low_risk / med_risk / late_po / high_risk / no_data
    """
    if not job_nums:
        return {}

    job_set = set(job_nums)
    result: dict[str, str] = {j: "no_data" for j in job_nums}
    report = _cached_group_report("component")

    for row in report["rows"]:
        job = row.works_order
        if not job or job not in job_set:
            continue
        line_status = _row_status(
            row.net_required, row.shortage, row.stock_on_hand, row.job_released, row.po_exists,
        )
        if _MAT_STATUS_PRIORITY.get(line_status, 0) > _MAT_STATUS_PRIORITY.get(result[job], -1):
            result[job] = line_status

    return result


def get_mrp_pegging(
    search: Optional[str] = None,
    so_number: Optional[str] = None,
) -> dict:
    """
    Build MRP time-phased pegging view for materials matching a search or SO number.

    For each matching material shows: opening stock, CO receipts, PO receipts and
    requirements in date order with a running projected balance. Balance goes
    negative (is_short=True) when demand exceeds cumulative supply.

    Filters:
      so_number — show materials required by this SO (strips 2-char works order suffix)
      search    — ilike search on material code / description

    Returns {materials: [MrpMaterial], material_count: int, stock_imported: bool}
    """
    stock_map = _load_stock()

    if not search and not so_number:
        return {"materials": [], "material_count": 0, "stock_imported": bool(stock_map)}

    # ---- Determine which material codes to show ----
    material_codes: set[str] = set()

    if so_number:
        rows = (
            db.session.query(MaterialRequirementMain.material_code)
            .filter(
                MaterialRequirementMain.job_closed != True,
                MaterialRequirementMain.issued_complete != True,
                MaterialRequirementMain.so_number == so_number,
            )
            .distinct().all()
        )
        material_codes.update(r.material_code for r in rows if r.material_code)

    if search:
        term = f"%{search.strip()}%"
        rows = (
            db.session.query(MaterialRequirementMain.material_code)
            .filter(db.or_(
                MaterialRequirementMain.material_code.ilike(term),
                MaterialRequirementMain.material_description.ilike(term),
            ))
            .distinct().all()
        )
        material_codes.update(r.material_code for r in rows if r.material_code)

        rows = (
            db.session.query(Stock.part_num)
            .filter(db.or_(
                Stock.part_num.ilike(term),
                Stock.part_description.ilike(term),
            ))
            .all()
        )
        material_codes.update(r.part_num for r in rows if r.part_num)

    if not material_codes:
        return {"materials": [], "material_count": 0, "stock_imported": bool(stock_map)}

    mc_list = sorted(material_codes)

    # ---- Load all requirements for these materials ----
    main_reqs = (
        MaterialRequirementMain.query
        .filter(
            MaterialRequirementMain.job_closed != True,
            MaterialRequirementMain.issued_complete != True,
            MaterialRequirementMain.material_code.in_(mc_list),
        )
        .order_by(MaterialRequirementMain.due_date)
        .all()
    )
    po_rows = (
        PurchaseOrder.query
        .filter(
            PurchaseOrder.part_num.in_(mc_list),
            PurchaseOrder.outstanding_qty > 0,
        )
        .order_by(PurchaseOrder.due_date)
        .all()
    )

    # ---- Collect raw events per material ----
    raw_events: dict[str, list[dict]] = defaultdict(list)
    descriptions: dict[str, str] = {}

    for req in main_reqs:
        mc = req.material_code or ""
        descriptions[mc] = req.material_description or ""
        net_req = max(Decimal(0), (req.qty_for_order or Decimal(0)) - (req.qty_issued or Decimal(0)))
        if net_req > 0:
            raw_events[mc].append({
                "event_date": req.due_date,
                "row_type": "requirement",
                "reference": req.works_order or "",
                "source": "main",
                "department": req.warehouse_code or "",
                "demand": net_req,
                "receipt": None,
                "job_released": req.job_released,
                "job_firm": req.job_firm,
                "_sort": (2, req.due_date or date.max, 1),
            })

    co_totals: dict[str, Decimal] = {}   # CO type not available in BAQ
    for po in po_rows:
        mc = po.part_num or ""
        qty = po.outstanding_qty or Decimal(0)
        raw_events[mc].append({
            "event_date": po.due_date,
            "row_type": "po",
            "reference": str(po.po_num) if po.po_num else "",
            "source": "po",
            "department": po.supplier_name or "",
            "demand": None,
            "receipt": qty,
            "_sort": (2, po.due_date or date.max, 0),
        })

    # ---- Build MrpMaterial objects ----
    # Use the same properly-netted report as the rest of the app so status labels
    # are consistent. Build a (works_order, material_code) → worst-status lookup.
    report = _cached_unfiltered_report()
    netted_status: dict[tuple[str, str], str] = {}
    for _row in report["rows"]:
        if not _row.works_order or not _row.material_code:
            continue
        _key = (_row.works_order, _row.material_code)
        _st = _row_status(
            _row.net_required, _row.shortage, _row.stock_on_hand,
            _row.job_released, _row.po_exists,
        )
        if _MAT_STATUS_PRIORITY.get(_st, 0) > _MAT_STATUS_PRIORITY.get(netted_status.get(_key, "no_data"), -1):
            netted_status[_key] = _st
    materials: list[MrpMaterial] = []

    for mc in mc_list:
        opening_stock = stock_map.get(mc, Decimal(0))
        desc = descriptions.get(mc, "")
        if not desc:
            s = Stock.query.filter_by(part_num=mc).first()
            desc = s.part_description if s else ""

        co_total = co_totals.get(mc, Decimal(0))
        events_raw = sorted(raw_events.get(mc, []), key=lambda e: e["_sort"])
        # True if any PO receipt exists in the pegging timeline for this material.
        po_exists_in_pegging = any(e["row_type"] == "po" for e in events_raw)

        events: list[MrpEvent] = []
        running    = opening_stock
        po_applied = Decimal(0)  # cumulative PO receipts applied so far in the timeline

        # Opening stock row
        events.append(MrpEvent(
            event_date=None, row_type="opening", reference="Opening Stock",
            source="", department="", demand=None, receipt=opening_stock,
            balance=running, is_short=running < 0,
        ))

        # CO block (always-available, shown after opening)
        if co_total > 0:
            running += co_total
            events.append(MrpEvent(
                event_date=None, row_type="co", reference="Call-Off Orders",
                source="co", department="", demand=None, receipt=co_total,
                balance=running, is_short=running < 0,
            ))

        # Dated events: PO receipts then requirements (same-date POs land first)
        for e in events_raw:
            if e["row_type"] == "po":
                running    += e["receipt"]
                po_applied += e["receipt"]
            else:
                running -= e["demand"]
                netted_st = netted_status.get((e["reference"], mc))
                is_short  = running < 0
                if is_short:
                    # Pegging balance has gone negative.  Distinguish two cases:
                    #   po_applied > 0  → PO already in the balance but still short:
                    #                    quantity is insufficient → Shortage
                    #   po_applied == 0 → future PO exists but hasn't arrived yet:
                    #                    timing issue → Late PO
                    if po_applied > 0 or not po_exists_in_pegging:
                        pegging_st = "high_risk"
                    else:
                        pegging_st = "late_po"
                    e["_mat_status"] = (
                        pegging_st
                        if _MAT_STATUS_PRIORITY.get(pegging_st, 0)
                           > _MAT_STATUS_PRIORITY.get(netted_st or "no_data", -1)
                        else netted_st
                    )
                else:
                    e["_mat_status"] = netted_st
            events.append(MrpEvent(
                event_date=e["event_date"],
                row_type=e["row_type"],
                reference=e["reference"],
                source=e["source"],
                department=e["department"],
                demand=e["demand"],
                receipt=e["receipt"],
                balance=running,
                is_short=running < 0,
                job_released=e.get("job_released"),
                job_firm=e.get("job_firm"),
                mat_status=e.get("_mat_status"),
            ))

        has_shortage = any(ev.is_short for ev in events)
        req_statuses = [
            ev.mat_status for ev in events
            if ev.row_type == "requirement" and ev.mat_status
        ]
        worst_status = max(
            req_statuses,
            key=lambda s: _MAT_STATUS_PRIORITY.get(s, -1),
            default="no_data",
        )
        materials.append(MrpMaterial(
            material_code=mc,
            description=desc,
            opening_stock=opening_stock,
            has_shortage=has_shortage,
            events=events,
            mat_status=worst_status,
        ))

    # Shortages first, then alphabetical
    materials.sort(key=lambda m: (0 if m.has_shortage else 1, m.material_code))

    return {
        "materials": materials,
        "material_count": len(materials),
        "stock_imported": bool(stock_map),
    }


# ---------------------------------------------------------------------------
# Weekly availability summary (for dashboard)
# ---------------------------------------------------------------------------

def get_weekly_so_breakdown(weeks_ahead: int = 12) -> dict:
    """
    Aggregate open SOs by ISO week and material status, returning both
    SO count and total order value per bucket.

    "Open"  = SalesOrder.open_order == True  (Epicor API data, daily sync).
    Date    = req_date (OrderRel_ReqDate) — the customer-requested delivery date.
    Value   = sum of release_price_gbp, de-duplicated to one row per
              (order_num, order_line, rel_num) before summing, so that
              multiple assemblies/jobs against the same release don't
              double-count the value.
    SO#     = str(order_num) — matches MaterialRequirementMain.so_number exactly.

    no_data status is folded into "ok" (no MRP requirements = no shortage risk).

    Returns:
        {
            "weeks":       [{"iso_key", "week_label", "week_start",
                             "ok", "low_risk", "med_risk", "high_risk",  ← each {"count", "value"}
                             "total_count", "total_value"}, ...],
            "totals":      {"ok": {"count", "value"}, ...},
            "total_value": Decimal,
            "total_count": int,
            "has_data":    bool,
        }
    """
    from datetime import date, timedelta
    from app.sales.orders.models import SalesOrder

    today   = date.today()
    cutoff  = today + timedelta(weeks=weeks_ahead)

    STATUSES = ("ok", "low_risk", "med_risk", "late_po", "high_risk")

    # Collapse to one row per (order_num, order_line, rel_num) to avoid
    # counting the same release price multiple times across assemblies/jobs.
    release_subq = (
        db.session.query(
            SalesOrder.order_num,
            SalesOrder.req_date,
            SalesOrder.release_price_gbp,
        )
        .filter(
            SalesOrder.open_order == True,
            SalesOrder.req_date.isnot(None),
            SalesOrder.req_date <= cutoff,
        )
        .group_by(
            SalesOrder.order_num,
            SalesOrder.order_line,
            SalesOrder.rel_num,
            SalesOrder.req_date,
            SalesOrder.release_price_gbp,
        )
        .subquery()
    )

    # One row per SO number: earliest req_date across all its releases, total GBP value.
    so_rows = (
        db.session.query(
            release_subq.c.order_num,
            func.min(release_subq.c.req_date).label("due_date"),
            func.sum(release_subq.c.release_price_gbp).label("total_value"),
        )
        .group_by(release_subq.c.order_num)
        .all()
    )

    if not so_rows:
        return {
            "weeks": [], "totals": {s: {"count": 0, "value": Decimal(0)} for s in STATUSES},
            "total_value": Decimal(0), "total_count": 0, "has_data": False,
        }

    # str(order_num) matches MaterialRequirementMain.so_number (stored as numeric string)
    so_numbers  = [str(r.order_num) for r in so_rows]
    status_map  = get_so_material_status(so_numbers)

    # ---- Group by ISO week ----
    # Weeks before the current ISO week collapse into a single "Overdue" bucket.
    iso_y_now, iso_w_now, _ = today.isocalendar()
    current_week_start = date.fromisocalendar(iso_y_now, iso_w_now, 1)

    def _empty_bucket():
        return {s: {"count": 0, "value": Decimal(0)} for s in STATUSES}

    buckets: dict[str, dict] = {}
    OVERDUE_KEY = "0000-W00"

    for r in so_rows:
        d      = r.due_date
        raw_st = status_map.get(str(r.order_num), "no_data")
        status = "ok" if raw_st == "no_data" else raw_st
        value  = r.total_value or Decimal(0)

        if d < current_week_start:
            key = OVERDUE_KEY
            if key not in buckets:
                b = _empty_bucket()
                b["iso_key"]    = key
                b["week_label"] = "Overdue"
                b["week_start"] = date.min
                b["is_overdue"] = True
                b["due_from"]   = None
                b["due_before"] = today.isoformat()
                buckets[key]    = b
        else:
            iso_y, iso_w, _ = d.isocalendar()
            key        = f"{iso_y}-W{iso_w:02d}"
            week_start = date.fromisocalendar(iso_y, iso_w, 1)
            week_end   = week_start + timedelta(days=6)

            if key not in buckets:
                b = _empty_bucket()
                b["iso_key"]    = key
                b["week_label"] = f"W{iso_w:02d}  {week_start.strftime('%d %b')}"
                b["week_start"] = week_start
                b["is_overdue"] = False
                b["due_from"]   = week_start.isoformat()
                b["due_before"] = week_end.isoformat()
                buckets[key]    = b

        buckets[key][status]["count"] += 1
        buckets[key][status]["value"] += value

    weeks = sorted(buckets.values(), key=lambda b: b["iso_key"])
    for b in weeks:
        b["total_count"] = sum(b[s]["count"] for s in STATUSES)
        b["total_value"] = sum(b[s]["value"] for s in STATUSES)

    # ---- Aggregate totals across all weeks ----
    totals = {s: {"count": 0, "value": Decimal(0)} for s in STATUSES}
    for b in weeks:
        for s in STATUSES:
            totals[s]["count"] += b[s]["count"]
            totals[s]["value"] += b[s]["value"]

    total_value = sum(totals[s]["value"] for s in STATUSES)
    total_count = sum(totals[s]["count"] for s in STATUSES)

    return {
        "weeks":       weeks,
        "totals":      totals,
        "total_value": total_value,
        "total_count": total_count,
        "has_data":    bool(weeks),
    }


def get_weekly_availability_summary(weeks_ahead: int = 12) -> list[dict]:
    """
    Aggregate netted shortage data by ISO week for the materials dashboard chart.

    Runs the full cumulative netting (same logic as get_shortage_report) then
    groups by due_date ISO week.  Only includes weeks from this week onward up
    to `weeks_ahead` weeks out, plus any overdue (past) weeks that still have
    open requirements.

    Each returned dict:
        iso_key       — 'YYYY-WNN' sortable string
        week_label    — 'Wnn  dd Mon' display string
        week_start    — Monday date of that week
        total_lines   — total requirement lines in the week
        ok_lines      — lines with zero shortage
        shortage_lines — lines with shortage > 0
        shortage_pct  — shortage_lines / total_lines * 100
        total_shortage_qty — sum of shortage Decimals
    """
    from datetime import timedelta

    # Use request-level cache to share the netting result with other callers
    report = _cached_unfiltered_report()
    rows = report["rows"]

    today = date.today()
    # ISO week of today
    today_iso = today.isocalendar()
    cutoff = today + timedelta(weeks=weeks_ahead)

    # Group by ISO year+week
    buckets: dict[str, dict] = defaultdict(lambda: {
        "total_lines": 0,
        "ok_lines": 0,
        "shortage_lines": 0,
        "total_shortage_qty": Decimal(0),
        "week_start": None,
        "week_label": "",
        "iso_key": "",
    })

    for row in rows:
        d = row.due_date
        if d is None:
            continue
        if d > cutoff:
            continue  # beyond our horizon

        iso_y, iso_w, _ = d.isocalendar()
        key = f"{iso_y}-W{iso_w:02d}"

        # Monday of that week
        week_start = date.fromisocalendar(iso_y, iso_w, 1)

        b = buckets[key]
        b["iso_key"] = key
        b["week_start"] = week_start
        b["week_label"] = f"W{iso_w:02d}  {week_start.strftime('%d %b')}"
        b["total_lines"] += 1

        if row.shortage > 0:
            b["shortage_lines"] += 1
            b["total_shortage_qty"] += row.shortage
        else:
            b["ok_lines"] += 1

    result = sorted(buckets.values(), key=lambda b: b["iso_key"])

    for b in result:
        total = b["total_lines"]
        b["shortage_pct"] = round(b["shortage_lines"] / total * 100, 1) if total else 0.0

    return result


# ---------------------------------------------------------------------------
# MRP Exempt Materials management
# ---------------------------------------------------------------------------

def get_exempt_materials(search: Optional[str] = None):
    """Return all exempt materials, optionally filtered by code/reason."""
    q = MrpExemptMaterial.query.order_by(MrpExemptMaterial.material_code)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                MrpExemptMaterial.material_code.ilike(term),
                MrpExemptMaterial.reason.ilike(term),
            )
        )
    return q.all()


def add_exemptions(codes: list[str], reason: Optional[str], user_id: Optional[int]) -> dict:
    """
    Add material codes to the exempt list. Ignores duplicates.

    Returns {"added": int, "skipped": int}.
    """
    from datetime import datetime, timezone
    added = skipped = 0
    reason = reason.strip() if reason else None
    for raw in codes:
        code = raw.strip().upper()
        if not code:
            continue
        existing = db.session.get(MrpExemptMaterial, code)
        if existing:
            skipped += 1
        else:
            db.session.add(MrpExemptMaterial(
                material_code=code,
                reason=reason,
                exempted_at=datetime.now(timezone.utc),
                exempted_by_id=user_id,
            ))
            added += 1
    db.session.commit()
    return {"added": added, "skipped": skipped}


def remove_exemptions(codes: list[str]) -> int:
    """Remove material codes from the exempt list. Returns count deleted."""
    deleted = 0
    for raw in codes:
        code = raw.strip().upper()
        if not code:
            continue
        obj = db.session.get(MrpExemptMaterial, code)
        if obj:
            db.session.delete(obj)
            deleted += 1
    db.session.commit()
    return deleted


def _has_reqs(group: str = "fabric") -> bool:
    return bool(
        db.session.query(func.count(MaterialRequirementMain.id))
        .filter(MaterialRequirementMain.material_group == group)
        .scalar()
    )


# ---------------------------------------------------------------------------
# Stock overview
# ---------------------------------------------------------------------------

def get_stock_summary() -> dict:
    """Return headline stock stats for the materials dashboard."""
    total = db.session.query(func.count(Stock.id)).scalar() or 0
    zero_stock = (
        db.session.query(func.count(Stock.id))
        .filter(Stock.qty_on_hand <= 0)
        .scalar() or 0
    )
    total_po_lines = db.session.query(func.count(PurchaseOrder.id)).scalar() or 0
    main_req_count = db.session.query(func.count(MaterialRequirementMain.id)).scalar() or 0

    # Actual netted shortage count — fabric group, shared with other page-load callers.
    cached           = _cached_unfiltered_report()
    shortage_estimate = sum(1 for r in cached["rows"] if r.shortage > 0)

    # Component shortage count — separate netting pass, also cached per request.
    comp_cached           = _cached_group_report("component")
    comp_shortage_estimate = sum(1 for r in comp_cached["rows"] if r.shortage > 0)

    from app.sales.orders.models import ImportBatch
    last_sync = (
        ImportBatch.query
        .filter_by(import_type="epicor_stock", status="success")
        .order_by(ImportBatch.uploaded_at.desc())
        .first()
    )

    return {
        "stock_lines":     total,
        "zero_stock":      zero_stock,
        "po_lines":        total_po_lines,
        "main_reqs":       main_req_count,
        "shortage_est":      shortage_estimate,
        "comp_shortage_est": comp_shortage_estimate,
        "last_sync":       last_sync,
    }


# ---------------------------------------------------------------------------
# Stock overview (for stock list page)
# ---------------------------------------------------------------------------

def get_stock_overview() -> dict:
    """
    Return class-level breakdown and summary KPIs for the Stock On Hand page.

    Returns:
        {
            "total_lines":   int,
            "zero_stock":    int,
            "in_deficit":    int,
            "classes":       [{"class_id", "count", "deficit_count", "total_qty"}, ...],
        }
    """
    total_lines = db.session.query(func.count(Stock.id)).scalar() or 0
    zero_stock = (
        db.session.query(func.count(Stock.id))
        .filter(Stock.qty_on_hand <= 0)
        .scalar() or 0
    )
    in_deficit = (
        db.session.query(func.count(Stock.id))
        .filter(Stock.insufficient_stock == True)
        .scalar() or 0
    )

    class_rows = (
        db.session.query(
            Stock.class_id,
            func.count(Stock.id).label("count"),
            func.coalesce(func.sum(Stock.qty_on_hand), 0).label("total_qty"),
            func.sum(
                func.cast(Stock.insufficient_stock == True, db.Integer)
            ).label("deficit_count"),
        )
        .group_by(Stock.class_id)
        .order_by(func.count(Stock.id).desc())
        .all()
    )
    classes = [
        {
            "class_id":     r.class_id or "—",
            "count":        r.count,
            "total_qty":    float(r.total_qty or 0),
            "deficit_count": int(r.deficit_count or 0),
        }
        for r in class_rows
    ]
    return {
        "total_lines": total_lines,
        "zero_stock":  zero_stock,
        "in_deficit":  in_deficit,
        "classes":     classes,
    }


# ---------------------------------------------------------------------------
# Shortage insight charts (for shortage report page)
# ---------------------------------------------------------------------------

def get_shortage_insights(rows: list) -> dict:
    """
    Derive chart data from already-computed shortage rows.

    Returns:
        {
            "top_materials": [{"code", "description", "shortage", "earliest_due"}, ...],  # top 10 by shortage qty
            "by_class":      [{"class_id", "shortage_qty", "line_count"}, ...],            # grouped by class_id
            "total_shortage_qty": Decimal,
            "unique_materials":   int,
        }
    """
    # All at-risk rows (any non-ok status) — basis for all insight computations
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
                "code": mc,
                "description": r.description,
                "shortage": Decimal(0),
                "earliest_due": r.due_date,
            }
        mat_totals[mc]["shortage"] += r.shortage
        if r.due_date and (mat_totals[mc]["earliest_due"] is None or r.due_date < mat_totals[mc]["earliest_due"]):
            mat_totals[mc]["earliest_due"] = r.due_date

    top_materials = sorted(mat_totals.values(), key=lambda x: x["shortage"], reverse=True)[:10]

    # ---- At-risk lines by material class ----
    class_totals: dict[str, dict] = {}
    for r in at_risk_rows:
        cid = r.class_id or "Unknown"
        if cid not in class_totals:
            class_totals[cid] = {"class_id": cid, "shortage_qty": Decimal(0), "line_count": 0}
        class_totals[cid]["shortage_qty"] += r.shortage
        class_totals[cid]["line_count"] += 1

    by_class = sorted(class_totals.values(), key=lambda x: x["shortage_qty"], reverse=True)

    total_shortage_qty = sum(r.shortage for r in short_rows)

    # ---- Per-material summary (all at-risk statuses) ----
    mat_summary: dict[str, dict] = {}
    for r in at_risk_rows:
        mc = r.material_code
        if mc not in mat_summary:
            mat_summary[mc] = {
                "material_code":    mc,
                "description":      r.description,
                "class_id":         r.class_id,
                "worst_status":     r.status,
                "jobs":             set(),
                "total_shortage":   Decimal(0),
                "total_po_cover":   Decimal(0),
                "earliest_due":     r.due_date,
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
        "total_shortage_qty": total_shortage_qty,
        "unique_materials":   len(mat_totals),
        "status_counts":      status_counts,
        "material_summary":   material_summary,
    }


# ---------------------------------------------------------------------------
# Supplier delivery intelligence (for new supplier page)
# ---------------------------------------------------------------------------

def get_supplier_delivery(weeks_ahead: int = 8) -> dict:
    """
    Per-supplier summary of open and overdue PO lines for the
    Supplier Delivery Intelligence page.

    Returns:
        {
            "suppliers": [
                {
                    "name":           str,
                    "total_lines":    int,
                    "total_value":    Decimal,
                    "overdue_lines":  int,
                    "overdue_value":  Decimal,
                    "this_week_lines": int,
                    "this_week_value": Decimal,
                    "overdue_pct":    float,   # 0-100
                }, ...
            ],
            "weeks":          same structure as get_purchasing_dashboard()["weeks"],
            "total_overdue_value": Decimal,
            "total_value":         Decimal,
            "has_data":            bool,
        }
    """
    from datetime import timedelta

    today = date.today()
    iso_y_now, iso_w_now, _ = today.isocalendar()
    current_week_start = date.fromisocalendar(iso_y_now, iso_w_now, 1)
    current_week_end   = current_week_start + timedelta(days=6)

    rows = (
        db.session.query(
            PurchaseOrder.supplier_name,
            PurchaseOrder.supplier_id,
            PurchaseOrder.due_date,
            PurchaseOrder.outstanding_qty,
            PurchaseOrder.unit_cost,
            PurchaseOrder.cost_per_code,
            PurchaseOrder.part_num,
        )
        .filter(PurchaseOrder.outstanding_qty > 0)
        .all()
    )

    if not rows:
        return {"suppliers": [], "total_overdue_value": Decimal(0), "total_value": Decimal(0), "has_data": False}

    from collections import defaultdict

    # Same cost_per_code divisor logic as get_purchasing_dashboard()
    _DIVISORS: dict[str, Decimal] = {"C": Decimal(100), "M": Decimal(1000)}

    sup: dict[str, dict] = {}
    for r in rows:
        name    = r.supplier_name or r.supplier_id or "Unknown"
        qty     = r.outstanding_qty or Decimal(0)
        divisor = _DIVISORS.get(r.cost_per_code or "E", Decimal(1))
        val     = qty * (r.unit_cost or Decimal(0)) / divisor

        if name not in sup:
            sup[name] = {
                "name": name,
                "total_lines": 0,  "total_value": Decimal(0),
                "overdue_lines": 0, "overdue_value": Decimal(0),
                "this_week_lines": 0, "this_week_value": Decimal(0),
            }
        sup[name]["total_lines"]  += 1
        sup[name]["total_value"]  += val

        is_overdue = r.due_date and r.due_date < today
        is_this_week = r.due_date and current_week_start <= r.due_date <= current_week_end

        if is_overdue:
            sup[name]["overdue_lines"] += 1
            sup[name]["overdue_value"] += val
        if is_this_week:
            sup[name]["this_week_lines"] += 1
            sup[name]["this_week_value"] += val

    suppliers = sorted(sup.values(), key=lambda s: s["overdue_value"], reverse=True)
    for s in suppliers:
        s["overdue_pct"] = (
            round(float(s["overdue_value"] / s["total_value"] * 100), 1)
            if s["total_value"] else 0.0
        )

    total_value         = sum(s["total_value"] for s in suppliers)
    total_overdue_value = sum(s["overdue_value"] for s in suppliers)

    return {
        "suppliers":            suppliers,
        "total_value":          total_value,
        "total_overdue_value":  total_overdue_value,
        "has_data":             bool(suppliers),
    }


# ---------------------------------------------------------------------------
# Purchasing dashboard
# ---------------------------------------------------------------------------

def get_purchasing_dashboard(weeks_ahead: int = 8) -> dict:
    """
    Headline PO KPIs, weekly delivery schedule, and top-supplier spend
    for the purchasing dashboard.

    PO value = outstanding_qty * unit_cost  (unit_cost is base/GBP currency).

    Returns:
        {
            "total_lines":    int,
            "total_value":    Decimal,        # GBP
            "overdue_lines":  int,
            "overdue_value":  Decimal,         # GBP
            "weeks":          [{"iso_key", "week_label", "value", "count", "is_overdue"}, ...],
            "top_suppliers":  [{"name", "value", "lines",
                                "overdue_value", "overdue_lines"}, ...],
            "has_data":       bool,
        }
    """
    from datetime import timedelta

    today = date.today()
    iso_y_now, iso_w_now, _ = today.isocalendar()
    current_week_start = date.fromisocalendar(iso_y_now, iso_w_now, 1)
    cutoff = today + timedelta(weeks=weeks_ahead)

    rows = (
        db.session.query(
            PurchaseOrder.supplier_name,
            PurchaseOrder.due_date,
            PurchaseOrder.outstanding_qty,
            PurchaseOrder.unit_cost,
            PurchaseOrder.cost_per_code,
        )
        .filter(PurchaseOrder.outstanding_qty > 0)
        .all()
    )

    if not rows:
        return {
            "total_lines": 0,   "total_value": Decimal(0),
            "overdue_lines": 0, "overdue_value": Decimal(0),
            "weeks": [],        "top_suppliers": [], "has_data": False,
        }

    _DIVISORS: dict[str, Decimal] = {"C": Decimal(100), "M": Decimal(1000)}

    total_lines  = 0
    total_value  = Decimal(0)
    overdue_lines = 0
    overdue_value = Decimal(0)
    week_buckets: dict[str, dict]     = {}
    supplier_buckets: dict[str, dict] = {}
    OVERDUE_KEY = "0000-W00"

    for r in rows:
        divisor = _DIVISORS.get(r.cost_per_code or "E", Decimal(1))
        val = (r.outstanding_qty or Decimal(0)) * (r.unit_cost or Decimal(0)) / divisor
        total_lines += 1
        total_value += val

        is_overdue = bool(r.due_date and r.due_date < today)
        if is_overdue:
            overdue_lines += 1
            overdue_value += val

        # ---- Week bucketing (overdue + next N weeks) ----
        due = r.due_date
        if due is not None and due <= cutoff:
            if due < current_week_start:
                key = OVERDUE_KEY
                if key not in week_buckets:
                    week_buckets[key] = {
                        "iso_key": key, "week_label": "Overdue",
                        "value": Decimal(0), "count": 0, "is_overdue": True,
                    }
            else:
                iso_y, iso_w, _ = due.isocalendar()
                key        = f"{iso_y}-W{iso_w:02d}"
                week_start = date.fromisocalendar(iso_y, iso_w, 1)
                if key not in week_buckets:
                    week_buckets[key] = {
                        "iso_key":    key,
                        "week_label": f"W{iso_w:02d}  {week_start.strftime('%d %b')}",
                        "week_start": week_start.isoformat(),
                        "week_end":   (week_start + timedelta(days=6)).isoformat(),
                        "value": Decimal(0), "count": 0, "is_overdue": False,
                    }
            week_buckets[key]["value"] += val
            week_buckets[key]["count"] += 1

        # ---- Supplier bucketing ----
        supplier = r.supplier_name or "Unknown"
        if supplier not in supplier_buckets:
            supplier_buckets[supplier] = {
                "name": supplier, "value": Decimal(0), "lines": 0,
                "overdue_value": Decimal(0), "overdue_lines": 0,
            }
        supplier_buckets[supplier]["value"]  += val
        supplier_buckets[supplier]["lines"]  += 1
        if is_overdue:
            supplier_buckets[supplier]["overdue_value"] += val
            supplier_buckets[supplier]["overdue_lines"] += 1

    weeks = sorted(week_buckets.values(), key=lambda b: b["iso_key"])
    top_suppliers = sorted(supplier_buckets.values(), key=lambda s: -s["value"])[:10]

    return {
        "total_lines":   total_lines,
        "total_value":   total_value,
        "overdue_lines": overdue_lines,
        "overdue_value": overdue_value,
        "weeks":         weeks,
        "top_suppliers": top_suppliers,
        "has_data":      True,
    }


# ---------------------------------------------------------------------------
# PO list
# ---------------------------------------------------------------------------

def get_po_list(
    search: Optional[str] = None,
    due_from: Optional[date] = None,
    due_before: Optional[date] = None,
    page: int = 1,
    per_page: int = 50,
):
    """Return paginated purchase orders, optionally filtered by text search and/or due-date range."""
    q = PurchaseOrder.query.order_by(PurchaseOrder.due_date.asc().nullslast(), PurchaseOrder.po_num)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                PurchaseOrder.part_num.ilike(term),
                PurchaseOrder.supplier_name.ilike(term),
                PurchaseOrder.line_desc.ilike(term),
            )
        )
    if due_from:
        q = q.filter(PurchaseOrder.due_date >= due_from)
    if due_before:
        q = q.filter(PurchaseOrder.due_date <= due_before)
    return q.paginate(page=page, per_page=per_page, error_out=False)


# ---------------------------------------------------------------------------
# Stock search
# ---------------------------------------------------------------------------

def get_stock_list(search: Optional[str] = None, page: int = 1, per_page: int = 50, class_filter: Optional[str] = None):
    """Return paginated stock lines, optionally filtered by search and class."""
    q = Stock.query.order_by(Stock.insufficient_stock.desc(), Stock.part_num)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                Stock.part_num.ilike(term),
                Stock.part_description.ilike(term),
            )
        )
    if class_filter:
        q = q.filter(Stock.class_id == class_filter)
    return q.paginate(page=page, per_page=per_page, error_out=False)
