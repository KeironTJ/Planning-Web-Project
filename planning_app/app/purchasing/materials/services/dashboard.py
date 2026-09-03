"""
Dashboard and weekly aggregation services.

Covers: purchasing dashboard KPIs, supplier delivery intelligence,
weekly SO breakdown by material status, and weekly shortage availability summary.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from ..models import PurchaseOrder
from ._cache import _cached_unfiltered_report
from .status import get_so_material_status

__all__ = [
    "get_purchasing_dashboard",
    "get_supplier_delivery",
    "get_weekly_so_breakdown",
    "get_weekly_availability_summary",
]


def get_purchasing_dashboard(weeks_ahead: int = 8) -> dict:
    """
    Headline PO KPIs, weekly delivery schedule, and top-supplier spend.

    PO value = outstanding_qty * unit_cost / cost_per_code_divisor (GBP).

    Returns:
        {
            "total_lines":    int,
            "total_value":    Decimal,
            "overdue_lines":  int,
            "overdue_value":  Decimal,
            "weeks":          [{"iso_key", "week_label", "value", "count", "is_overdue"}, ...],
            "top_suppliers":  [{"name", "value", "lines", "overdue_value", "overdue_lines"}, ...],
            "has_data":       bool,
        }
    """
    today = date.today()
    iso_y_now, iso_w_now, _ = today.isocalendar()
    current_week_start = date.fromisocalendar(iso_y_now, iso_w_now, 1)
    cutoff = today + timedelta(weeks=weeks_ahead)
    OVERDUE_KEY = "0000-W00"

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

    total_lines   = 0
    total_value   = Decimal(0)
    overdue_lines = 0
    overdue_value = Decimal(0)
    week_buckets: dict[str, dict]     = {}
    supplier_buckets: dict[str, dict] = {}

    for r in rows:
        divisor = _DIVISORS.get(r.cost_per_code or "E", Decimal(1))
        val = (r.outstanding_qty or Decimal(0)) * (r.unit_cost or Decimal(0)) / divisor
        total_lines += 1
        total_value += val

        is_overdue = bool(r.due_date and r.due_date < today)
        if is_overdue:
            overdue_lines += 1
            overdue_value += val

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

        supplier = r.supplier_name or "Unknown"
        if supplier not in supplier_buckets:
            supplier_buckets[supplier] = {
                "name": supplier, "value": Decimal(0), "lines": 0,
                "overdue_value": Decimal(0), "overdue_lines": 0,
            }
        supplier_buckets[supplier]["value"] += val
        supplier_buckets[supplier]["lines"] += 1
        if is_overdue:
            supplier_buckets[supplier]["overdue_value"] += val
            supplier_buckets[supplier]["overdue_lines"] += 1

    weeks         = sorted(week_buckets.values(), key=lambda b: b["iso_key"])
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


def get_supplier_delivery(weeks_ahead: int = 8) -> dict:
    """
    Per-supplier summary of open and overdue PO lines.

    Returns:
        {
            "suppliers": [
                {
                    "name", "total_lines", "total_value",
                    "overdue_lines", "overdue_value",
                    "this_week_lines", "this_week_value",
                    "overdue_pct",
                }, ...
            ],
            "total_overdue_value": Decimal,
            "total_value":         Decimal,
            "has_data":            bool,
        }
    """
    today = date.today()
    iso_y_now, iso_w_now, _ = today.isocalendar()
    current_week_start = date.fromisocalendar(iso_y_now, iso_w_now, 1)
    current_week_end   = current_week_start + timedelta(days=6)

    _DIVISORS: dict[str, Decimal] = {"C": Decimal(100), "M": Decimal(1000)}

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
        return {
            "suppliers": [], "total_overdue_value": Decimal(0),
            "total_value": Decimal(0), "has_data": False,
        }

    sup: dict[str, dict] = {}
    for r in rows:
        name    = r.supplier_name or r.supplier_id or "Unknown"
        qty     = r.outstanding_qty or Decimal(0)
        divisor = _DIVISORS.get(r.cost_per_code or "E", Decimal(1))
        val     = qty * (r.unit_cost or Decimal(0)) / divisor

        if name not in sup:
            sup[name] = {
                "name": name,
                "total_lines": 0,    "total_value": Decimal(0),
                "overdue_lines": 0,  "overdue_value": Decimal(0),
                "this_week_lines": 0, "this_week_value": Decimal(0),
            }
        sup[name]["total_lines"] += 1
        sup[name]["total_value"] += val

        is_overdue   = r.due_date and r.due_date < today
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

    return {
        "suppliers":            suppliers,
        "total_value":          sum(s["total_value"] for s in suppliers),
        "total_overdue_value":  sum(s["overdue_value"] for s in suppliers),
        "has_data":             bool(suppliers),
    }


def get_weekly_so_breakdown(weeks_ahead: int = 12) -> dict:
    """
    Aggregate open SOs by ISO week and material status, returning SO count and
    total order value per bucket.

    "Open"  = SalesOrder.open_order == True (Epicor API data, daily sync).
              Fully-shipped releases (shipped_qty >= selling_qty) are excluded —
              they stay "open" in Epicor until invoiced, but have no remaining
              material need, so counting them inflates the Overdue bucket.
    Date    = req_date (OrderRel_ReqDate) — the customer-requested delivery date.
    Value   = sum of release_price_gbp, de-duplicated per (order_num, order_line,
              rel_num) so multiple assemblies/jobs don't double-count the value.
    SO#     = str(order_num) — matches MaterialRequirementMain.so_number exactly.

    no_data status is folded into "ok" (no MRP requirements = no shortage risk).

    Returns:
        {
            "weeks":       [{"iso_key", "week_label", "week_start",
                             "ok", "low_risk", "med_risk", "high_risk",  <- each {"count","value"}
                             "total_count", "total_value"}, ...],
            "totals":      {"ok": {"count", "value"}, ...},
            "total_value": Decimal,
            "total_count": int,
            "has_data":    bool,
        }
    """
    from app.sales.orders.models import SalesOrder  # lazy — avoids circular at import time

    today   = date.today()
    cutoff  = today + timedelta(weeks=weeks_ahead)
    STATUSES = ("ok", "low_risk", "med_risk", "late_po", "high_risk")

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
            # Exclude releases that are already fully shipped — these stay
            # "open" in Epicor until invoiced but have no remaining material
            # need, so counting them inflates the Overdue bucket.
            db.or_(
                SalesOrder.selling_qty.is_(None),
                SalesOrder.shipped_qty.is_(None),
                SalesOrder.shipped_qty < SalesOrder.selling_qty,
            ),
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

    so_numbers = [str(r.order_num) for r in so_rows]
    status_map = get_so_material_status(so_numbers)

    iso_y_now, iso_w_now, _ = today.isocalendar()
    current_week_start = date.fromisocalendar(iso_y_now, iso_w_now, 1)
    OVERDUE_KEY = "0000-W00"

    def _empty_bucket():
        return {s: {"count": 0, "value": Decimal(0)} for s in STATUSES}

    buckets: dict[str, dict] = {}

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

    totals = {s: {"count": 0, "value": Decimal(0)} for s in STATUSES}
    for b in weeks:
        for s in STATUSES:
            totals[s]["count"] += b[s]["count"]
            totals[s]["value"] += b[s]["value"]

    return {
        "weeks":       weeks,
        "totals":      totals,
        "total_value": sum(totals[s]["value"] for s in STATUSES),
        "total_count": sum(totals[s]["count"] for s in STATUSES),
        "has_data":    bool(weeks),
    }


def get_weekly_availability_summary(weeks_ahead: int = 12) -> list[dict]:
    """
    Aggregate netted shortage data by ISO week for the materials dashboard chart.

    Each returned dict:
        iso_key            — 'YYYY-WNN' sortable string
        week_label         — 'Wnn  dd Mon' display string
        week_start         — Monday date of that week
        total_lines        — total requirement lines in the week
        ok_lines           — lines with zero shortage
        shortage_lines     — lines with shortage > 0
        shortage_pct       — shortage_lines / total_lines * 100
        total_shortage_qty — sum of shortage Decimals
    """
    report = _cached_unfiltered_report()
    rows   = report["rows"]

    today  = date.today()
    cutoff = today + timedelta(weeks=weeks_ahead)

    buckets: dict[str, dict] = defaultdict(lambda: {
        "total_lines": 0, "ok_lines": 0, "shortage_lines": 0,
        "total_shortage_qty": Decimal(0),
        "week_start": None, "week_label": "", "iso_key": "",
    })

    for row in rows:
        d = row.due_date
        if d is None or d > cutoff:
            continue

        iso_y, iso_w, _ = d.isocalendar()
        key        = f"{iso_y}-W{iso_w:02d}"
        week_start = date.fromisocalendar(iso_y, iso_w, 1)

        b = buckets[key]
        b["iso_key"]    = key
        b["week_start"] = week_start
        b["week_label"] = f"W{iso_w:02d}  {week_start.strftime('%d %b')}"
        b["total_lines"] += 1

        if row.shortage > 0:
            b["shortage_lines"]     += 1
            b["total_shortage_qty"] += row.shortage
        else:
            b["ok_lines"] += 1

    result = sorted(buckets.values(), key=lambda b: b["iso_key"])
    for b in result:
        total = b["total_lines"]
        b["shortage_pct"] = round(b["shortage_lines"] / total * 100, 1) if total else 0.0

    return result
