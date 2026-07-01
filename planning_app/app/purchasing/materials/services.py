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
from datetime import date
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


@dataclass
class MrpMaterial:
    material_code: str
    description: str
    opening_stock: Decimal
    has_shortage: bool
    events: list


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_exempt_codes() -> frozenset[str]:
    """Return the set of material codes exempt from MRP shortage calculations."""
    rows = db.session.query(MrpExemptMaterial.material_code).all()
    return frozenset(r.material_code for r in rows)


def _load_stock() -> dict[str, Decimal]:
    """Return {product_code: qty_on_hand} for all stock rows."""
    rows = db.session.query(Stock.part_num, Stock.qty_on_hand).all()
    return {r.part_num: (r.qty_on_hand or Decimal(0)) for r in rows}


def _load_po_coverage() -> dict[str, list[tuple[date, Decimal]]]:
    """
    Return {part_num: [(due_date, outstanding_qty), ...]} for all open PO releases.
    Date-constrained: only rows with a due_date are included.
    """
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
        coverage[r.part_num].append((r.due_date, r.outstanding_qty or Decimal(0)))
    return dict(coverage)


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
    source: str = "all",          # "main" | "all" (both map to main; retained for URL compat)
    dept_filter: Optional[str] = None,
    search: Optional[str] = None,
    shortages_only: bool = True,
    due_before: Optional[date] = None,
    due_from: Optional[date] = None,
) -> dict:
    """
    Compute material shortages using cumulative MRP netting.

    Netting logic:
      1. Collect ALL requirements for the given source (no display filters yet).
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
    stock_map    = _load_stock()
    po_map       = _load_po_coverage()   # actual POs, date-constrained
    co_qty_map   = _load_co_qty()        # CO call-offs, treated as finite pool
    exempt_codes = _load_exempt_codes()  # materials excluded from shortage reporting

    # ---- Phase 1: collect ALL raw requirements ----
    raw: list[dict] = []

    for req in (
        MaterialRequirementMain.query
        .filter(
            MaterialRequirementMain.job_closed != True,
            MaterialRequirementMain.issued_complete != True,
        )
        .order_by(MaterialRequirementMain.due_date)
        .all()
    ):
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
        # Sort by due_date (None → treated as very far future)
        reqs.sort(key=lambda r: (r["due_date"] or date.max))

        remaining_stock = stock_map.get(mc, Decimal(0))
        remaining_co    = co_qty_map.get(mc, Decimal(0))
        po_lines        = sorted(po_map.get(mc, []), key=lambda x: x[0])  # (date, qty)
        po_consumed     = Decimal(0)

        for req in reqs:
            net_req = req["net_required"]
            req_due = req["due_date"]

            # PO quantity available up to this requirement's due date,
            # minus what earlier requirements in this material group already consumed
            po_gross = sum(
                (qty for d, qty in po_lines if req_due is None or d <= req_due),
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
            all_netted.append(req)

    # ---- Phase 3: apply display filters and build ShortageRows ----
    search_term = search.strip().lower() if search else None

    rows: list[ShortageRow] = []
    for r in all_netted:
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
        ))

    # Sort by due_date, then worst shortage first
    rows.sort(key=lambda r: (r.due_date or date.max, -r.shortage))

    shortage_count = sum(1 for r in rows if r.shortage > 0)

    return {
        "rows":           rows,
        "total_rows":     len(rows),
        "shortage_count": shortage_count,
        "stock_imported": bool(stock_map),
        "reqs_imported":  bool(raw) or _has_reqs(),
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
    "high_risk":  3,
}

#: Display metadata: status -> (label, Bootstrap colour)
MAT_STATUS_META: dict[str, tuple[str, str]] = {
    "ok":        ("Mat. OK",    "success"),
    "low_risk":  ("Low Risk",   "info"),
    "med_risk":  ("Med Risk",   "warning"),
    "high_risk": ("Shortage",   "danger"),
    "no_data":   ("—",          "secondary"),
}


def _apply_coverage(
    result: dict[str, str],
    reqs: list,
    so_key_fn,
    material_code_fn,
    net_req_fn,
    due_date_fn,
    stock_map: dict,
    co_qty_map: dict,
    po_entries: dict,
    plan_start_map: Optional[dict] = None,
    exempt_codes: frozenset = frozenset(),
) -> None:
    """
    Apply worst-case coverage tier to result dict for a list of requirement rows.

    For the PO date constraint, the effective deadline is:
      - plan_start_map[so]  if a planned start date has been set (planner's date)
      - req.due_date        otherwise (ERP's MRP date)
    Using the planned start date ensures PO coverage is assessed against when
    production is actually scheduled to begin, not the ERP's estimate.
    """
    plan_start_map = plan_start_map or {}
    for req in reqs:
        so = so_key_fn(req)
        if not so or so not in result:
            continue

        mc = material_code_fn(req) or ""
        # Exempt materials are treated as fully covered — skip to avoid false positives
        if mc in exempt_codes:
            if result[so] == "no_data":
                result[so] = "ok"
            continue
        net_req = net_req_fn(req)

        if net_req == 0:
            line_status = "ok"
        else:
            remaining = net_req - stock_map.get(mc, Decimal(0))
            if remaining <= 0:
                line_status = "ok"
            else:
                remaining -= co_qty_map.get(mc, Decimal(0))
                if remaining <= 0:
                    line_status = "low_risk"
                else:
                    # Use planner's start date if set; fall back to ERP MRP date
                    effective_date = plan_start_map.get(so) or due_date_fn(req)
                    po_cov = sum(
                        (qty for d, qty in po_entries.get(mc, [])
                         if effective_date is None or d <= effective_date),
                        Decimal(0),
                    )
                    remaining -= po_cov
                    line_status = "high_risk" if remaining > 0 else "med_risk"

        if _MAT_STATUS_PRIORITY.get(line_status, 0) > _MAT_STATUS_PRIORITY.get(result[so], -1):
            result[so] = line_status


def get_so_material_status(
    so_numbers: list[str],
    plan_start_map: Optional[dict] = None,
) -> dict[str, str]:
    """
    Compute material availability status for a list of SO numbers.

    Checks both MainMaterialReq (main production) and ASMaterialReq (after sales).
    The ERP encodes works order refs as SOPNO + 2-digit line suffix, so the join
    strips the last 2 characters: works_order[:-2] == so_number.

    plan_start_map: optional {so_number: date} of planned production start dates.
        When provided, PO coverage is checked against the planned start date rather
        than the ERP's MRP due date — so materials are assessed against when
        production is actually scheduled to begin.

    Coverage tiers (worst-case across all requirement lines for the SO):
        ok        — net requirement fully covered by stock on hand
        low_risk  — remaining covered by Call-Off orders (Type=CO, open-ended)
        med_risk  — remaining covered by actual POs (Type=PO, due <= effective date)
        high_risk — still uncovered after stock + CO + PO
        no_data   — no MRP requirements found for this SO

    Note: CO orders are treated as always-available (no date constraint) because
    their ERP due dates are set to a far-future placeholder (~2099).
    """
    if not so_numbers:
        return {}

    result: dict[str, str] = {so: "no_data" for so in so_numbers}

    # ---- Query main production requirements via direct so_number field ----
    main_reqs = (
        MaterialRequirementMain.query
        .filter(
            MaterialRequirementMain.job_closed != True,
            MaterialRequirementMain.issued_complete != True,
            MaterialRequirementMain.so_number.in_(so_numbers),
        )
        .all()
    )

    if not main_reqs:
        return result

    # ---- Load stock + PO coverage maps ----
    exempt_codes = _load_exempt_codes()
    stock_map = _load_stock()

    po_rows = (
        db.session.query(
            PurchaseOrder.part_num,
            PurchaseOrder.due_date,
            PurchaseOrder.outstanding_qty,
        )
        .filter(
            PurchaseOrder.part_num.isnot(None),
            PurchaseOrder.outstanding_qty > 0,
        )
        .all()
    )

    co_qty_map: dict[str, Decimal] = {}   # CO type not available in BAQ
    po_entries: dict[str, list[tuple]] = defaultdict(list)

    for r in po_rows:
        if not r.part_num:
            continue
        qty = r.outstanding_qty or Decimal(0)
        if r.due_date:
            po_entries[r.part_num].append((r.due_date, qty))

    # ---- Apply coverage — main requirements ----
    _apply_coverage(
        result, main_reqs,
        so_key_fn=lambda r: r.so_number or "",
        material_code_fn=lambda r: r.material_code,
        net_req_fn=lambda r: max(
            Decimal(0),
            (r.qty_for_order or Decimal(0)) - (r.qty_issued or Decimal(0)),
        ),
        due_date_fn=lambda r: r.due_date,
        stock_map=stock_map,
        co_qty_map=co_qty_map,
        po_entries=po_entries,
        plan_start_map=plan_start_map,
        exempt_codes=exempt_codes,
    )

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
                "department": req.department or "",
                "demand": net_req,
                "receipt": None,
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
    materials: list[MrpMaterial] = []

    for mc in mc_list:
        opening_stock = stock_map.get(mc, Decimal(0))
        desc = descriptions.get(mc, "")
        if not desc:
            s = Stock.query.filter_by(part_num=mc).first()
            desc = s.part_description if s else ""

        co_total = co_totals.get(mc, Decimal(0))
        events_raw = sorted(raw_events.get(mc, []), key=lambda e: e["_sort"])

        events: list[MrpEvent] = []
        running = opening_stock

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
                running += e["receipt"]
            else:
                running -= e["demand"]
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
            ))

        has_shortage = any(ev.is_short for ev in events)
        materials.append(MrpMaterial(
            material_code=mc,
            description=desc,
            opening_stock=opening_stock,
            has_shortage=has_shortage,
            events=events,
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

    "Open" = has at least one non-closed WorksOrderOperation.
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
    from app.sales.orders.models import SalesOrderLine, WorksOrderOperation

    today   = date.today()
    cutoff  = today + timedelta(weeks=weeks_ahead)

    STATUSES = ("ok", "low_risk", "med_risk", "high_risk")

    open_so_subq = (
        db.session.query(WorksOrderOperation.so_number)
        .filter(WorksOrderOperation.status != WorksOrderOperation.STATUS_CLOSED)
        .distinct()
        .subquery()
    )

    so_rows = (
        db.session.query(
            SalesOrderLine.so_number,
            func.min(SalesOrderLine.due_date).label("due_date"),
            func.sum(SalesOrderLine.total_value).label("total_value"),
        )
        .filter(
            SalesOrderLine.so_number.in_(db.session.query(open_so_subq.c.so_number)),
            SalesOrderLine.due_date.isnot(None),
            SalesOrderLine.due_date <= cutoff,
        )
        .group_by(SalesOrderLine.so_number)
        .all()
    )

    if not so_rows:
        return {
            "weeks": [], "totals": {s: {"count": 0, "value": Decimal(0)} for s in STATUSES},
            "total_value": Decimal(0), "total_count": 0, "has_data": False,
        }

    so_numbers  = [r.so_number for r in so_rows]
    status_map  = get_so_material_status(so_numbers)

    # ---- Group by ISO week ----
    def _empty_bucket():
        return {s: {"count": 0, "value": Decimal(0)} for s in STATUSES}

    buckets: dict[str, dict] = {}

    for r in so_rows:
        d      = r.due_date
        raw_st = status_map.get(r.so_number, "no_data")
        status = "ok" if raw_st == "no_data" else raw_st
        value  = r.total_value or Decimal(0)

        iso_y, iso_w, _ = d.isocalendar()
        key        = f"{iso_y}-W{iso_w:02d}"
        week_start = date.fromisocalendar(iso_y, iso_w, 1)

        if key not in buckets:
            b = _empty_bucket()
            b["iso_key"]    = key
            b["week_label"] = f"W{iso_w:02d}  {week_start.strftime('%d %b')}"
            b["week_start"] = week_start
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

    # Run full netting with no display filters
    report = get_shortage_report(source="all", shortages_only=False)
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


def _has_reqs() -> bool:
    return bool(db.session.query(func.count(MaterialRequirementMain.id)).scalar())


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

    # Quick shortage count (only materials with net_req > 0)
    # Use a lightweight version — just count main reqs where qty_for_order > qty_issued
    shortage_estimate = (
        db.session.query(func.count(MaterialRequirementMain.id))
        .filter(
            MaterialRequirementMain.job_closed != True,
            MaterialRequirementMain.issued_complete != True,
            MaterialRequirementMain.qty_for_order > MaterialRequirementMain.qty_issued,
        )
        .scalar() or 0
    )

    from app.sales.orders.models import ImportBatch
    last_stock_import = (
        ImportBatch.query
        .filter_by(import_type=ImportBatch.TYPE_STOCK, status="success")
        .order_by(ImportBatch.uploaded_at.desc())
        .first()
    )

    return {
        "stock_lines":     total,
        "zero_stock":      zero_stock,
        "po_lines":        total_po_lines,
        "main_reqs":       main_req_count,
        "shortage_est":    shortage_estimate,
        "last_stock_import": last_stock_import,
    }


# ---------------------------------------------------------------------------
# PO list
# ---------------------------------------------------------------------------

def get_po_list(search: Optional[str] = None, page: int = 1, per_page: int = 50):
    """Return paginated purchase orders, optionally filtered."""
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
    return q.paginate(page=page, per_page=per_page, error_out=False)


# ---------------------------------------------------------------------------
# Stock search
# ---------------------------------------------------------------------------

def get_stock_list(search: Optional[str] = None, page: int = 1, per_page: int = 50):
    """Return paginated stock lines, optionally filtered."""
    q = Stock.query.order_by(Stock.part_num)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                Stock.part_num.ilike(term),
                Stock.part_description.ilike(term),
            )
        )
    return q.paginate(page=page, per_page=per_page, error_out=False)
