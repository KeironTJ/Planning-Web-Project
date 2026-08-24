"""
MRP time-phased pegging view.

For each matching material shows: opening stock, CO receipts, PO receipts and
requirements in date order with a running projected balance.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from app.extensions import db
from ..models import MaterialRequirementMain, PurchaseOrder, Stock
from ._cache import _cached_unfiltered_report
from .loaders import _get_group_lead_days, _load_stock
from .netting import _row_status
from .types import MrpEvent, MrpMaterial, _MAT_STATUS_PRIORITY

__all__ = ["get_mrp_pegging"]


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
      so_number — show materials required by this SO
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

    # ---- Load all requirements and POs for these materials ----
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
    lead_days_by_group = {
        "fabric":    _get_group_lead_days("fabric"),
        "component": _get_group_lead_days("component"),
    }
    mat_lead_days: dict[str, int] = {}

    for req in main_reqs:
        mc = req.material_code or ""
        descriptions[mc] = req.material_description or ""
        ld = lead_days_by_group.get(req.material_group or "fabric", lead_days_by_group["fabric"])
        mat_lead_days.setdefault(mc, ld)
        net_req = max(Decimal(0), (req.qty_for_order or Decimal(0)) - (req.qty_issued or Decimal(0)))
        if net_req > 0:
            raw_events[mc].append({
                "event_date":    req.due_date,
                "effective_date": (req.due_date - timedelta(days=ld)) if req.due_date else None,
                "row_type":      "requirement",
                "reference":     req.works_order or "",
                "source":        "main",
                "department":    req.warehouse_code or "",
                "demand":        net_req,
                "receipt":       None,
                "job_released":  req.job_released,
                "job_firm":      req.job_firm,
                "_sort":         (2, req.due_date or date.max, 1, (req.works_order or "").strip()),
            })

    co_totals: dict[str, Decimal] = {}  # CO type not available in BAQ
    for po in po_rows:
        mc = po.part_num or ""
        qty = po.outstanding_qty or Decimal(0)
        ld = mat_lead_days.get(mc, lead_days_by_group["fabric"])
        po_coverage_from = (po.due_date + timedelta(days=ld)) if po.due_date else None
        raw_events[mc].append({
            "event_date":    po.due_date,
            "effective_date": po_coverage_from,
            "row_type":      "po",
            "reference":     str(po.po_num) if po.po_num else "",
            "source":        "po",
            "department":    po.supplier_name or "",
            "demand":        None,
            "receipt":       qty,
            "_sort":         (2, po.due_date or date.max, 0),
        })

    # ---- Build per-(works_order, material_code) netted status lookup ----
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

    # ---- Build MrpMaterial objects ----
    materials: list[MrpMaterial] = []

    for mc in mc_list:
        opening_stock = stock_map.get(mc, Decimal(0))
        desc = descriptions.get(mc, "")
        if not desc:
            s = Stock.query.filter_by(part_num=mc).first()
            desc = s.part_description if s else ""

        co_total   = co_totals.get(mc, Decimal(0))
        events_raw = sorted(raw_events.get(mc, []), key=lambda e: e["_sort"])
        po_exists_in_pegging = any(e["row_type"] == "po" for e in events_raw)

        events: list[MrpEvent] = []
        running    = opening_stock
        po_applied = Decimal(0)

        events.append(MrpEvent(
            event_date=None, row_type="opening", reference="Opening Stock",
            source="", department="", demand=None, receipt=opening_stock,
            balance=running, is_short=running < 0,
        ))

        if co_total > 0:
            running += co_total
            events.append(MrpEvent(
                event_date=None, row_type="co", reference="Call-Off Orders",
                source="co", department="", demand=None, receipt=co_total,
                balance=running, is_short=running < 0,
            ))

        for e in events_raw:
            if e["row_type"] == "po":
                running    += e["receipt"]
                po_applied += e["receipt"]
            else:
                running -= e["demand"]
                netted_st = netted_status.get((e["reference"], mc))
                is_short  = running < 0
                if is_short:
                    # po_applied > 0 → PO landed but still short → genuine shortage
                    # po_applied == 0 → future PO exists but hasn't arrived → timing issue
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
                effective_date=e.get("effective_date"),
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

    materials.sort(key=lambda m: (0 if m.has_shortage else 1, m.material_code))

    return {
        "materials":      materials,
        "material_count": len(materials),
        "stock_imported": bool(stock_map),
    }
