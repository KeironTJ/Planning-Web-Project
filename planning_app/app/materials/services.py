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
    MaterialRequirementAfterSales,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ShortageRow:
    source: str              # "main" | "aftersales"
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
    order_number: Optional[str] = None
    customer_id: Optional[str] = None
    customer: Optional[str] = None
    complete: Optional[str] = None


@dataclass
class MrpEvent:
    event_date: Optional[date]
    row_type: str          # "opening" | "co" | "po" | "requirement"
    reference: str
    source: str            # "main" | "aftersales" | "po" | "co" | ""
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

def _load_stock() -> dict[str, Decimal]:
    """Return {product_code: qty_on_hand} for all stock rows."""
    rows = db.session.query(Stock.product_code, Stock.qty_on_hand).all()
    return {r.product_code: (r.qty_on_hand or Decimal(0)) for r in rows}


def _load_po_coverage() -> dict[str, list[tuple[date, Decimal]]]:
    """
    Return {product_code: [(due_date, outstanding_qty), ...]} for actual POs only (Type=PO).
    CO (call-off) orders are excluded here — use _load_co_qty() for those.
    Used to compute date-constrained PO coverage up to any given due date.
    """
    rows = (
        db.session.query(
            PurchaseOrder.product_code,
            PurchaseOrder.due_date,
            PurchaseOrder.outstanding_qty,
        )
        .filter(
            PurchaseOrder.product_code.isnot(None),
            PurchaseOrder.outstanding_qty > 0,
            PurchaseOrder.po_type != "CO",
        )
        .order_by(PurchaseOrder.product_code, PurchaseOrder.due_date)
        .all()
    )
    coverage: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for r in rows:
        if r.due_date is not None:
            coverage[r.product_code].append((r.due_date, r.outstanding_qty or Decimal(0)))
    return dict(coverage)


def _load_co_qty() -> dict[str, Decimal]:
    """
    Return {product_code: total_outstanding_qty} for Call-Off orders (Type=CO).

    CO orders have ERP due dates set to a far-future placeholder (~2099) meaning
    the stock is available on demand. They are not date-constrained.
    """
    rows = (
        db.session.query(PurchaseOrder.product_code, PurchaseOrder.outstanding_qty)
        .filter(
            PurchaseOrder.product_code.isnot(None),
            PurchaseOrder.outstanding_qty > 0,
            PurchaseOrder.po_type == "CO",
        )
        .all()
    )
    co_qty: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        co_qty[r.product_code] += (r.outstanding_qty or Decimal(0))
    return dict(co_qty)


# ---------------------------------------------------------------------------
# Shortage calculation
# ---------------------------------------------------------------------------

def get_shortage_report(
    source: str = "all",          # "main" | "aftersales" | "all"
    dept_filter: Optional[str] = None,
    search: Optional[str] = None,
    shortages_only: bool = True,
    due_before: Optional[date] = None,
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
    stock_map  = _load_stock()
    po_map     = _load_po_coverage()   # actual POs, date-constrained
    co_qty_map = _load_co_qty()        # CO call-offs, treated as finite pool

    # ---- Phase 1: collect ALL raw requirements (source filter only) ----
    raw: list[dict] = []

    if source in ("all", "main"):
        for req in (
            MaterialRequirementMain.query
            .filter(MaterialRequirementMain.complete != "Y")
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
                "department":   req.department or "",
                "due_date":     req.due_date,
                "qty_required": qty_req,
                "qty_issued":   qty_issued,
                "net_required": net_req,
                "works_order":  req.works_order,
                "order_number": None,
                "customer_id":  req.customer_id,
                "customer":     None,
                "complete":     req.complete,
                "_search_text": f"{mc} {req.material_description or ''} {req.works_order or ''}".lower(),
            })

    if source in ("all", "aftersales"):
        for req in (
            MaterialRequirementAfterSales.query
            .order_by(MaterialRequirementAfterSales.due_date)
            .all()
        ):
            pc      = req.product_code or ""
            net_req = req.qty_required or Decimal(0)
            raw.append({
                "source":       "aftersales",
                "material_code": pc,
                "description":  req.description or "",
                "department":   req.department or "",
                "due_date":     req.due_date,
                "qty_required": net_req,
                "qty_issued":   Decimal(0),
                "net_required": net_req,
                "works_order":  None,
                "order_number": req.order_number,
                "customer_id":  None,
                "customer":     req.customer,
                "complete":     None,
                "_search_text": f"{pc} {req.description or ''} {req.order_number or ''}".lower(),
            })

    # ---- Phase 2: cumulative netting per material ----
    by_material: dict[str, list[dict]] = defaultdict(list)
    for r in raw:
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
            order_number=r["order_number"],
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
        "reqs_imported":  bool(raw) or _has_reqs(source),
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


def _so_from_works_order(works_order: Optional[str]) -> Optional[str]:
    """
    Extract the SO number from a works order reference.

    The ERP encodes works orders as SOPNO + zero-padded ORDITEM suffix
    (e.g. works_order "53054801" = SO "530548" + line "01").
    We strip the last 2 characters to recover the SO number.
    """
    if works_order and len(works_order) > 2:
        return works_order[:-2]
    return None


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

    # ---- Query main production requirements ----
    # works_order = SOPNO + 2-digit suffix; strip suffix to match SO number
    so_col_main = func.substr(
        MaterialRequirementMain.works_order,
        1,
        func.length(MaterialRequirementMain.works_order) - 2,
    )
    main_reqs = (
        MaterialRequirementMain.query
        .filter(
            MaterialRequirementMain.complete != "Y",
            MaterialRequirementMain.works_order.isnot(None),
            so_col_main.in_(so_numbers),
        )
        .all()
    )

    # ---- Query after-sales requirements ----
    so_col_as = func.substr(
        MaterialRequirementAfterSales.order_number,
        1,
        func.length(MaterialRequirementAfterSales.order_number) - 2,
    )
    as_reqs = (
        MaterialRequirementAfterSales.query
        .filter(
            MaterialRequirementAfterSales.order_number.isnot(None),
            so_col_as.in_(so_numbers),
        )
        .all()
    )

    if not main_reqs and not as_reqs:
        return result

    # ---- Load stock + PO coverage maps ----
    stock_map = _load_stock()

    po_rows = (
        db.session.query(
            PurchaseOrder.product_code,
            PurchaseOrder.due_date,
            PurchaseOrder.outstanding_qty,
            PurchaseOrder.po_type,
        )
        .filter(
            PurchaseOrder.product_code.isnot(None),
            PurchaseOrder.outstanding_qty > 0,
        )
        .all()
    )

    co_qty_map: dict[str, Decimal] = defaultdict(Decimal)
    po_entries: dict[str, list[tuple]] = defaultdict(list)

    for r in po_rows:
        if not r.product_code:
            continue
        qty = r.outstanding_qty or Decimal(0)
        if r.po_type == "CO":
            co_qty_map[r.product_code] += qty
        elif r.due_date:
            po_entries[r.product_code].append((r.due_date, qty))

    # ---- Apply coverage — main requirements ----
    _apply_coverage(
        result, main_reqs,
        so_key_fn=lambda r: _so_from_works_order(r.works_order),
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
    )

    # ---- Apply coverage — after-sales requirements ----
    _apply_coverage(
        result, as_reqs,
        so_key_fn=lambda r: _so_from_works_order(r.order_number),
        material_code_fn=lambda r: r.product_code,
        net_req_fn=lambda r: r.qty_required or Decimal(0),
        due_date_fn=lambda r: r.due_date,
        stock_map=stock_map,
        co_qty_map=co_qty_map,
        po_entries=po_entries,
        plan_start_map=plan_start_map,
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
        so_col_main = func.substr(
            MaterialRequirementMain.works_order, 1,
            func.length(MaterialRequirementMain.works_order) - 2,
        )
        rows = (
            db.session.query(MaterialRequirementMain.material_code)
            .filter(
                MaterialRequirementMain.complete != "Y",
                MaterialRequirementMain.works_order.isnot(None),
                so_col_main == so_number,
            )
            .distinct().all()
        )
        material_codes.update(r.material_code for r in rows if r.material_code)

        so_col_as = func.substr(
            MaterialRequirementAfterSales.order_number, 1,
            func.length(MaterialRequirementAfterSales.order_number) - 2,
        )
        rows = (
            db.session.query(MaterialRequirementAfterSales.product_code)
            .filter(
                MaterialRequirementAfterSales.order_number.isnot(None),
                so_col_as == so_number,
            )
            .distinct().all()
        )
        material_codes.update(r.product_code for r in rows if r.product_code)

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
            db.session.query(MaterialRequirementAfterSales.product_code)
            .filter(db.or_(
                MaterialRequirementAfterSales.product_code.ilike(term),
                MaterialRequirementAfterSales.description.ilike(term),
            ))
            .distinct().all()
        )
        material_codes.update(r.product_code for r in rows if r.product_code)

        rows = (
            db.session.query(Stock.product_code)
            .filter(db.or_(
                Stock.product_code.ilike(term),
                Stock.description.ilike(term),
            ))
            .all()
        )
        material_codes.update(r.product_code for r in rows if r.product_code)

    if not material_codes:
        return {"materials": [], "material_count": 0, "stock_imported": bool(stock_map)}

    mc_list = sorted(material_codes)

    # ---- Load all requirements for these materials ----
    main_reqs = (
        MaterialRequirementMain.query
        .filter(
            MaterialRequirementMain.complete != "Y",
            MaterialRequirementMain.material_code.in_(mc_list),
        )
        .order_by(MaterialRequirementMain.due_date)
        .all()
    )
    as_reqs = (
        MaterialRequirementAfterSales.query
        .filter(MaterialRequirementAfterSales.product_code.in_(mc_list))
        .order_by(MaterialRequirementAfterSales.due_date)
        .all()
    )
    po_rows = (
        PurchaseOrder.query
        .filter(
            PurchaseOrder.product_code.in_(mc_list),
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

    for req in as_reqs:
        pc = req.product_code or ""
        if not descriptions.get(pc):
            descriptions[pc] = req.description or ""
        net_req = req.qty_required or Decimal(0)
        if net_req > 0:
            raw_events[pc].append({
                "event_date": req.due_date,
                "row_type": "requirement",
                "reference": req.order_number or "",
                "source": "aftersales",
                "department": req.department or "",
                "demand": net_req,
                "receipt": None,
                "_sort": (2, req.due_date or date.max, 1),
            })

    co_totals: dict[str, Decimal] = defaultdict(Decimal)
    for po in po_rows:
        mc = po.product_code or ""
        qty = po.outstanding_qty or Decimal(0)
        if po.po_type == "CO":
            co_totals[mc] += qty
        else:
            raw_events[mc].append({
                "event_date": po.due_date,
                "row_type": "po",
                "reference": po.po_number or "",
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
            s = Stock.query.filter_by(product_code=mc).first()
            desc = s.description if s else ""

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


def _has_reqs(source: str) -> bool:
    if source in ("all", "main"):
        if db.session.query(func.count(MaterialRequirementMain.id)).scalar():
            return True
    if source in ("all", "aftersales"):
        if db.session.query(func.count(MaterialRequirementAfterSales.id)).scalar():
            return True
    return False


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
    as_req_count   = db.session.query(func.count(MaterialRequirementAfterSales.id)).scalar() or 0

    # Quick shortage count (only materials with net_req > 0)
    # Use a lightweight version — just count main reqs where qty_for_order > qty_issued
    shortage_estimate = (
        db.session.query(func.count(MaterialRequirementMain.id))
        .filter(
            MaterialRequirementMain.complete != "Y",
            MaterialRequirementMain.qty_for_order > MaterialRequirementMain.qty_issued,
        )
        .scalar() or 0
    )

    from app.orders.models import ImportBatch
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
        "as_reqs":         as_req_count,
        "shortage_est":    shortage_estimate,
        "last_stock_import": last_stock_import,
    }


# ---------------------------------------------------------------------------
# PO list
# ---------------------------------------------------------------------------

def get_po_list(search: Optional[str] = None, page: int = 1, per_page: int = 50):
    """Return paginated purchase orders, optionally filtered."""
    q = PurchaseOrder.query.order_by(PurchaseOrder.due_date.asc().nullslast(), PurchaseOrder.po_number)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                PurchaseOrder.product_code.ilike(term),
                PurchaseOrder.po_number.ilike(term),
                PurchaseOrder.supplier_name.ilike(term),
                PurchaseOrder.description.ilike(term),
            )
        )
    return q.paginate(page=page, per_page=per_page, error_out=False)


# ---------------------------------------------------------------------------
# Stock search
# ---------------------------------------------------------------------------

def get_stock_list(search: Optional[str] = None, page: int = 1, per_page: int = 50):
    """Return paginated stock lines, optionally filtered."""
    q = Stock.query.order_by(Stock.product_code)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                Stock.product_code.ilike(term),
                Stock.description.ilike(term),
            )
        )
    return q.paginate(page=page, per_page=per_page, error_out=False)
