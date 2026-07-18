"""
Orders service layer.

Sales order review: open order book insights and visualisation.
Epicor ERP handles all processing; this module is purely for reading and
presenting sales order data.
"""

import math
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, case

from app.extensions import db
from app.core.exceptions import ValidationError
from .models import SalesOrderComment, SalesOrder

# A sale is countable as a "unit" only when the model is a real finished-goods
# product — NULL, empty string, and "Scatter" items are excluded from all unit
# counts and charts throughout this module.
_unit_qty_so = case(
    (db.and_(
        SalesOrder.model.isnot(None),
        SalesOrder.model != "",
        SalesOrder.model != "Scatter",
    ), SalesOrder.selling_qty),
    else_=0,
)


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

class SimplePagination:
    """Minimal pagination proxy for in-Python grouped queries."""

    def __init__(self, items: list, total: int, page: int, per_page: int):
        self.items    = items
        self.total    = total
        self.page     = page
        self.per_page = per_page

    @property
    def pages(self) -> int:
        return math.ceil(self.total / self.per_page) if self.per_page else 0

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    def iter_pages(self, left_edge=1, right_edge=1, left_current=2, right_current=2):
        last = 0
        for num in range(1, self.pages + 1):
            if (
                num <= left_edge
                or (self.page - left_current) <= num <= (self.page + right_current)
                or num > self.pages - right_edge
            ):
                if last + 1 != num:
                    yield None
                yield num
                last = num


# ---------------------------------------------------------------------------
# Open Order Book
# ---------------------------------------------------------------------------

def get_order_book(
    *,
    page: int = 1,
    per_page: int = 25,
    search: Optional[str] = None,
    order_type_filter: Optional[str] = None,
    customer_filter: Optional[str] = None,
    customer_po_filter: Optional[str] = None,
    country_filter: Optional[str] = None,
    customer_group_filter: Optional[str] = None,
    overdue_only: bool = False,
    order_by: str = "due_date",
    due_date_from: Optional[date] = None,
    due_date_to: Optional[date] = None,
) -> tuple["SimplePagination", list]:
    """
    Return open sales orders from SalesOrder, grouped by order_num.

    Each item in the returned list is a dict:
        so_number           str   (str representation of order_num)
        customer_code       str   (customer_id)
        customer_name       str
        customer_order_ref  str | None  (po_num)
        order_type          str   (so_type_desc)
        so_type             str   (so_type code, for badge styling)
        country             str   (customer_country)
        customer_group      str
        channel             str
        order_date          date | None
        due_date            date | None  (earliest need_by_date across releases)
        days_delta          int | None
        total_qty           float  (sum selling_qty, deduped by release)
        total_value         float  (sum release_price_gbp, deduped by release)
        line_count          int    (distinct order_line count)
    """
    today = date.today()
    dedup_sub = _so_dedup_subq(open_only=True)

    q = db.session.query(SalesOrder).join(dedup_sub, SalesOrder.id == dedup_sub.c.id)

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(db.or_(
            func.cast(SalesOrder.order_num, db.String).ilike(term),
            SalesOrder.customer_name.ilike(term),
            SalesOrder.part_num.ilike(term),
            SalesOrder.part_desc.ilike(term),
            SalesOrder.po_num.ilike(term),
        ))

    if order_type_filter:
        q = q.filter(SalesOrder.so_type_desc == order_type_filter)

    if customer_filter:
        q = q.filter(SalesOrder.customer_name.ilike(f"%{customer_filter.strip()}%"))

    if customer_po_filter:
        q = q.filter(SalesOrder.po_num.ilike(f"%{customer_po_filter.strip()}%"))

    if country_filter:
        q = q.filter(SalesOrder.customer_country == country_filter)

    if customer_group_filter:
        q = q.filter(SalesOrder.customer_group == customer_group_filter)

    if overdue_only:
        q = q.filter(
            SalesOrder.need_by_date < today,
            SalesOrder.need_by_date.isnot(None),
        )

    if due_date_from:
        q = q.filter(SalesOrder.need_by_date >= due_date_from)
    if due_date_to:
        q = q.filter(SalesOrder.need_by_date <= due_date_to)

    if order_by == "so_number":
        q = q.order_by(SalesOrder.order_num)
    elif order_by == "customer":
        q = q.order_by(
            SalesOrder.customer_name,
            SalesOrder.need_by_date.asc().nullslast(),
            SalesOrder.order_num,
        )
    else:  # due_date (default) or value — value is post-sorted in Python
        q = q.order_by(
            SalesOrder.need_by_date.asc().nullslast(),
            SalesOrder.order_num,
        )

    all_rows = q.all()

    # Group by order_num maintaining query order
    seen: dict[int, dict] = {}
    order_list: list[dict] = []
    for so in all_rows:
        if so.order_num not in seen:
            entry = {
                "so_number":          str(so.order_num),
                "customer_code":      so.customer_id or "",
                "customer_name":      so.customer_name or "",
                "customer_order_ref": so.po_num,
                "order_type":         so.so_type_desc or "",
                "so_type":            so.so_type or "",
                "country":            so.customer_country or "",
                "customer_group":     so.customer_group or "",
                "channel":            so.channel or "",
                "order_date":         so.order_date,
                "_releases":          [],
            }
            seen[so.order_num] = entry
            order_list.append(entry)
        seen[so.order_num]["_releases"].append(so)

    for entry in order_list:
        releases = entry.pop("_releases")
        due_dates = [r.need_by_date for r in releases if r.need_by_date]
        entry["due_date"]    = min(due_dates) if due_dates else None
        entry["days_delta"]  = (entry["due_date"] - today).days if entry["due_date"] else None
        entry["total_qty"]   = sum(float(r.selling_qty or 0) for r in releases)
        entry["total_value"] = sum(float(r.release_price_gbp or 0) for r in releases)
        entry["line_count"]  = len({r.order_line for r in releases})

    if order_by == "value":
        order_list.sort(key=lambda x: x["total_value"], reverse=True)

    total = len(order_list)
    start = (page - 1) * per_page
    page_items = order_list[start: start + per_page]
    return SimplePagination(page_items, total, page, per_page), page_items


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

def get_order_book_summary() -> dict:
    """
    High-level counts and values for the open order book banner.

    Returns:
        total        - distinct open order count
        overdue      - orders with earliest need_by_date in the past
        total_value  - sum of open release values (GBP, deduped by release)
        overdue_value
        total_units
    """
    today = date.today()
    dedup_sub = _so_dedup_subq(open_only=True)

    total = db.session.query(
        func.count(SalesOrder.order_num.distinct())
    ).filter(SalesOrder.open_order == True).scalar() or 0  # noqa: E712

    overdue = db.session.query(
        func.count(SalesOrder.order_num.distinct())
    ).filter(
        SalesOrder.open_order == True,  # noqa: E712
        SalesOrder.need_by_date < today,
        SalesOrder.need_by_date.isnot(None),
    ).scalar() or 0

    total_value = (
        db.session.query(func.sum(SalesOrder.release_price_gbp))
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .scalar() or 0.0
    )

    overdue_value = (
        db.session.query(func.sum(SalesOrder.release_price_gbp))
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(SalesOrder.need_by_date < today, SalesOrder.need_by_date.isnot(None))
        .scalar() or 0.0
    )

    total_units = (
        db.session.query(func.sum(_unit_qty_so))
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .scalar() or 0.0
    )

    return {
        "total":         total,
        "overdue":       overdue,
        "total_value":   float(total_value),
        "overdue_value": float(overdue_value),
        "total_units":   float(total_units),
    }


# ---------------------------------------------------------------------------
# Dashboard data
# ---------------------------------------------------------------------------

def _so_dedup_subq(open_only: bool = True):
    """
    Subquery returning min(id) per (order_num, order_line, rel_num).

    Collapses multi-assembly / multi-job rows so that value and qty
    aggregations are not double-counted.
    """
    q = db.session.query(func.min(SalesOrder.id).label("id"))
    if open_only:
        q = q.filter(SalesOrder.open_order == True)  # noqa: E712
    return q.group_by(
        SalesOrder.order_num, SalesOrder.order_line, SalesOrder.rel_num
    ).subquery()


def get_order_book_dashboard() -> dict:
    """
    Return aggregated data for the orders dashboard charts and KPIs.
    Data source: SalesOrder (Epicor bskyVA05v1 BAQ) for richer insights.

    Keys:
        summary           - {total, overdue, total_value, overdue_value,
                             total_units, held_count}
        due_by_week       - {labels, amounts, units}
        value_by_customer - top 15 [{customer, value, units}]
        by_product_group  - [{group, units, value}]  (keyed on prod_code)
        by_model          - top 15 [{model, units, value}]
        by_customer_group - [{group, units, value}]
        by_channel        - [{channel, units, value}]
        by_country        - top 15 [{country, units, value}]
        by_order_type     - [{order_type, units, value}]  (so_type_desc)
        lead_time         - {labels, counts}
        horizon_grid      - {week_labels, rows}
        intake_over_time  - {by_year, years, current_year, month_labels}
    """
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())

    # One row per (order_num, order_line, rel_num) for open orders
    dedup_sub = _so_dedup_subq(open_only=True)

    # ── Summary KPIs ──────────────────────────────────────────────────────────
    total = db.session.query(
        func.count(SalesOrder.order_num.distinct())
    ).filter(SalesOrder.open_order == True).scalar() or 0  # noqa: E712

    overdue = db.session.query(
        func.count(SalesOrder.order_num.distinct())
    ).filter(
        SalesOrder.open_order == True,  # noqa: E712
        SalesOrder.need_by_date < today,
        SalesOrder.need_by_date.isnot(None),
    ).scalar() or 0

    total_value = (
        db.session.query(func.sum(SalesOrder.release_price_gbp))
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .scalar() or 0.0
    )

    overdue_value = (
        db.session.query(func.sum(SalesOrder.release_price_gbp))
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(
            SalesOrder.need_by_date < today,
            SalesOrder.need_by_date.isnot(None),
        )
        .scalar() or 0.0
    )

    total_units = (
        db.session.query(func.sum(_unit_qty_so))
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .scalar() or 0.0
    )

    held_count = db.session.query(
        func.count(SalesOrder.order_num.distinct())
    ).filter(
        SalesOrder.open_order == True,  # noqa: E712
        db.or_(
            SalesOrder.order_held == True,  # noqa: E712
            SalesOrder.so_credit_hold == True,  # noqa: E712
            SalesOrder.customer_credit_hold == True,  # noqa: E712
        ),
    ).scalar() or 0

    summary = {
        "total":         total,
        "overdue":       overdue,
        "total_value":   float(total_value),
        "overdue_value": float(overdue_value),
        "total_units":   float(total_units),
        "held_count":    held_count,
    }

    # ── Due by week (grouped per order by earliest need_by_date) ─────────────
    week_labels = ["Overdue"]
    week_values = [0.0]
    week_units  = [0.0]
    week_starts = [None]
    for i in range(10):
        ws = this_monday + timedelta(weeks=i)
        iso_wk = ws.isocalendar()[1]
        week_labels.append(f"Wk {iso_wk}")
        week_values.append(0.0)
        week_units.append(0.0)
        week_starts.append(ws)

    so_due_rows = (
        db.session.query(
            SalesOrder.order_num,
            func.min(SalesOrder.need_by_date).label("min_due"),
            func.sum(SalesOrder.release_price_gbp).label("total_val"),
            func.sum(_unit_qty_so).label("total_qty"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .group_by(SalesOrder.order_num)
        .all()
    )

    for row in so_due_rows:
        if not row.min_due:
            continue
        val = float(row.total_val or 0)
        qty = float(row.total_qty or 0)
        if row.min_due < this_monday:
            bucket = 0
        else:
            bucket = next(
                (i for i, ws in enumerate(week_starts[1:], 1)
                 if ws <= row.min_due <= ws + timedelta(days=6)),
                None,
            )
        if bucket is not None:
            week_values[bucket] += val
            week_units[bucket]  += qty

    # ── Value by customer (top 15) ────────────────────────────────────────────
    value_by_customer_rows = (
        db.session.query(
            SalesOrder.customer_name,
            func.sum(SalesOrder.release_price_gbp).label("val"),
            func.sum(_unit_qty_so).label("cnt"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(SalesOrder.customer_name.isnot(None))
        .group_by(SalesOrder.customer_name)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .limit(15)
        .all()
    )

    # ── By product code (Epicor prod_code = product group) ────────────────────
    by_product_group_rows = (
        db.session.query(
            SalesOrder.prod_code,
            func.sum(_unit_qty_so).label("cnt"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(SalesOrder.prod_code.isnot(None))
        .group_by(SalesOrder.prod_code)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .all()
    )

    # ── By model (top 15 by value, excluding Scatter) ─────────────────────────
    by_model_rows = (
        db.session.query(
            SalesOrder.model,
            func.count(SalesOrder.order_num.distinct()).label("cnt"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(
            SalesOrder.model.isnot(None),
            SalesOrder.model != "",
            SalesOrder.model != "Scatter",
        )
        .group_by(SalesOrder.model)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .limit(15)
        .all()
    )

    # ── By customer group ─────────────────────────────────────────────────────
    by_customer_group_rows = (
        db.session.query(
            SalesOrder.customer_group,
            func.sum(_unit_qty_so).label("cnt"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(SalesOrder.customer_group.isnot(None))
        .group_by(SalesOrder.customer_group)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .all()
    )

    # ── By channel ────────────────────────────────────────────────────────────
    by_channel_rows = (
        db.session.query(
            SalesOrder.channel,
            func.sum(_unit_qty_so).label("cnt"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(SalesOrder.channel.isnot(None))
        .group_by(SalesOrder.channel)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .all()
    )

    # ── By country (top 15) ───────────────────────────────────────────────────
    by_country_rows = (
        db.session.query(
            SalesOrder.customer_country,
            func.sum(_unit_qty_so).label("cnt"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(SalesOrder.customer_country.isnot(None))
        .group_by(SalesOrder.customer_country)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .limit(15)
        .all()
    )

    # ── By SO type (so_type_desc replaces old order_type) ────────────────────
    by_order_type_rows = (
        db.session.query(
            SalesOrder.so_type_desc,
            func.sum(_unit_qty_so).label("cnt"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(SalesOrder.so_type_desc.isnot(None))
        .group_by(SalesOrder.so_type_desc)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .all()
    )

    # ── Lead time distribution (order_date → need_by_date) ───────────────────
    lt_rows = (
        db.session.query(
            SalesOrder.order_date,
            SalesOrder.need_by_date,
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(
            SalesOrder.order_date.isnot(None),
            SalesOrder.need_by_date.isnot(None),
        )
        .all()
    )
    lt_bucket_labels = ["<2 wks", "2–4 wks", "4–8 wks", "8–12 wks", "12–24 wks", "24 wks+"]
    lt_bucket_counts = [0, 0, 0, 0, 0, 0]
    for r in lt_rows:
        days = (r.need_by_date - r.order_date).days
        if   days <  14: lt_bucket_counts[0] += 1
        elif days <  28: lt_bucket_counts[1] += 1
        elif days <  56: lt_bucket_counts[2] += 1
        elif days <  84: lt_bucket_counts[3] += 1
        elif days < 168: lt_bucket_counts[4] += 1
        else:            lt_bucket_counts[5] += 1

    # ── 4-week horizon grid ───────────────────────────────────────────────────
    horizon_weeks = [this_monday + timedelta(weeks=i) for i in range(4)]
    horizon_labels = [f"Wk {ws.isocalendar()[1]}" for ws in horizon_weeks]
    horizon_raw = (
        db.session.query(
            SalesOrder.model,
            SalesOrder.need_by_date,
            SalesOrder.selling_qty,
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(
            SalesOrder.model.isnot(None),
            SalesOrder.model != "",
            SalesOrder.model != "Scatter",
            SalesOrder.need_by_date >= this_monday,
            SalesOrder.need_by_date < this_monday + timedelta(weeks=4),
        )
        .all()
    )
    horizon_by_model: dict[str, list] = {}
    for r in horizon_raw:
        if not r.need_by_date or not r.model:
            continue
        m = r.model
        if m not in horizon_by_model:
            horizon_by_model[m] = [0.0, 0.0, 0.0, 0.0]
        for i, ws in enumerate(horizon_weeks):
            if ws <= r.need_by_date <= ws + timedelta(days=6):
                horizon_by_model[m][i] += float(r.selling_qty or 0)
                break
    horizon_rows_sorted = sorted(
        [{"model": m, "weeks": wks, "total": sum(wks)}
         for m, wks in horizon_by_model.items()],
        key=lambda x: x["total"], reverse=True
    )[:15]

    # ── Order intake over time (all SalesOrders, deduped by release) ──────────
    current_year = date.today().year
    intake_start = date(current_year - 3, 1, 1)
    intake_dedup_sub = _so_dedup_subq(open_only=False)
    intake_raw = (
        db.session.query(
            func.strftime("%Y", SalesOrder.order_date).label("yr"),
            func.strftime("%m", SalesOrder.order_date).label("mn"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
            func.sum(_unit_qty_so).label("qty"),
        )
        .join(intake_dedup_sub, SalesOrder.id == intake_dedup_sub.c.id)
        .filter(
            SalesOrder.order_date.isnot(None),
            SalesOrder.order_date >= intake_start,
            SalesOrder.void_line.isnot(True),
        )
        .group_by(
            func.strftime("%Y", SalesOrder.order_date),
            func.strftime("%m", SalesOrder.order_date),
        )
        .order_by(
            func.strftime("%Y", SalesOrder.order_date),
            func.strftime("%m", SalesOrder.order_date),
        )
        .all()
    )
    _month_names = ["Jan","Feb","Mar","Apr","May","Jun",
                    "Jul","Aug","Sep","Oct","Nov","Dec"]
    intake_by_year: dict = {}
    for r in intake_raw:
        yr = r.yr
        if yr not in intake_by_year:
            intake_by_year[yr] = {"amounts": [0.0]*12, "units": [0.0]*12, "avg": [None]*12}
        m_idx = int(r.mn) - 1
        qty = float(r.qty or 0)
        val = round(float(r.val or 0), 2)
        intake_by_year[yr]["amounts"][m_idx] = val
        intake_by_year[yr]["units"][m_idx]   = round(qty, 0)
        intake_by_year[yr]["avg"][m_idx]     = round(val / qty, 2) if qty else None
    years_available = sorted(intake_by_year.keys())

    return {
        "summary": summary,
        "due_by_week": {
            "labels":  week_labels,
            "amounts": [round(v, 2) for v in week_values],
            "units":   [round(v, 0) for v in week_units],
        },
        "value_by_customer": [
            {"customer": r.customer_name, "value": float(r.val or 0), "units": float(r.cnt or 0)}
            for r in value_by_customer_rows
        ],
        "by_product_group": [
            {"group": r.prod_code, "units": float(r.cnt or 0), "value": float(r.val or 0)}
            for r in by_product_group_rows
        ],
        "by_model": [
            {"model": r.model, "units": float(r.cnt or 0), "value": float(r.val or 0)}
            for r in by_model_rows
        ],
        "by_customer_group": [
            {"group": r.customer_group, "units": float(r.cnt or 0), "value": float(r.val or 0)}
            for r in by_customer_group_rows
        ],
        "by_channel": [
            {"channel": r.channel, "units": float(r.cnt or 0), "value": float(r.val or 0)}
            for r in by_channel_rows
        ],
        "by_country": [
            {"country": r.customer_country, "units": float(r.cnt or 0), "value": float(r.val or 0)}
            for r in by_country_rows
        ],
        "by_order_type": [
            {"order_type": r.so_type_desc, "units": float(r.cnt or 0), "value": float(r.val or 0)}
            for r in by_order_type_rows
        ],
        "lead_time": {
            "labels": lt_bucket_labels,
            "counts": lt_bucket_counts,
        },
        "horizon_grid": {
            "week_labels": horizon_labels,
            "rows": horizon_rows_sorted,
        },
        "intake_over_time": {
            "by_year": intake_by_year,
            "years": years_available,
            "current_year": str(current_year),
            "month_labels": _month_names,
        },
    }


# ---------------------------------------------------------------------------
# Overdue orders
# ---------------------------------------------------------------------------

def get_overdue_orders(
    customer_filter: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    sort: str = "days_overdue",
) -> dict:
    """
    Return overdue open orders from SalesOrder for the overdue report view.
    An order is overdue when its earliest need_by_date is in the past.
    """
    today = date.today()
    dedup_sub = _so_dedup_subq(open_only=True)

    q = (
        db.session.query(
            SalesOrder.order_num,
            SalesOrder.customer_name,
            SalesOrder.so_type_desc,
            func.min(SalesOrder.need_by_date).label("min_due"),
            func.sum(SalesOrder.release_price_gbp).label("total_value"),
            func.count(SalesOrder.order_line.distinct()).label("line_count"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(
            SalesOrder.need_by_date < today,
            SalesOrder.need_by_date.isnot(None),
        )
        .group_by(
            SalesOrder.order_num,
            SalesOrder.customer_name,
            SalesOrder.so_type_desc,
        )
    )

    if customer_filter:
        q = q.filter(SalesOrder.customer_name.ilike(f"%{customer_filter.strip()}%"))

    rows = q.all()

    search_lower = search.lower() if search else None
    records = []
    for row in rows:
        if search_lower:
            if (
                search_lower not in str(row.order_num)
                and search_lower not in (row.customer_name or "").lower()
            ):
                continue
        days = (today - row.min_due).days if row.min_due else 0
        records.append({
            "so_number":     str(row.order_num),
            "customer_name": row.customer_name or "-",
            "order_type":    row.so_type_desc,
            "total_value":   float(row.total_value or 0),
            "min_due":       row.min_due,
            "days_overdue":  days,
            "line_count":    row.line_count,
        })

    _sort_fns = {
        "days_overdue": lambda r: -r["days_overdue"],
        "due_date":     lambda r: r["min_due"] or date.max,
        "value":        lambda r: -r["total_value"],
        "customer":     lambda r: r["customer_name"].lower(),
        "so_number":    lambda r: r["so_number"],
    }
    records.sort(key=_sort_fns.get(sort, _sort_fns["days_overdue"]))

    total = len(records)
    days_list = [r["days_overdue"] for r in records]
    start = (page - 1) * per_page
    pagination = SimplePagination(records[start: start + per_page], total, page, per_page)

    age_labels = ["1-7 days", "8-14 days", "15-30 days", "31-60 days", "60+ days"]
    age_counts = [0, 0, 0, 0, 0]
    for d in days_list:
        if d <= 7:    age_counts[0] += 1
        elif d <= 14: age_counts[1] += 1
        elif d <= 30: age_counts[2] += 1
        elif d <= 60: age_counts[3] += 1
        else:         age_counts[4] += 1

    value_by_cust_rows = (
        db.session.query(
            SalesOrder.customer_name,
            func.sum(SalesOrder.release_price_gbp).label("val"),
            func.count(SalesOrder.order_num.distinct()).label("cnt"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(
            SalesOrder.need_by_date < today,
            SalesOrder.need_by_date.isnot(None),
            SalesOrder.customer_name.isnot(None),
        )
        .group_by(SalesOrder.customer_name)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .limit(15)
        .all()
    )

    return {
        "total_overdue_orders": total,
        "total_overdue_value":  sum(r["total_value"] for r in records),
        "avg_days_overdue":     round(sum(days_list) / len(days_list)) if days_list else 0,
        "max_days_overdue":     max(days_list) if days_list else 0,
        "age_chart": {"labels": age_labels, "counts": age_counts},
        "value_by_customer": [
            {"customer": r.customer_name, "value": float(r.val or 0), "count": r.cnt}
            for r in value_by_cust_rows
        ],
        "pagination":     pagination,
        "orders":         pagination.items,
        "total_filtered": total,
    }


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_order_types() -> list[str]:
    """Return sorted list of distinct SO type descriptions from open orders."""
    rows = (
        db.session.query(SalesOrder.so_type_desc)
        .filter(SalesOrder.open_order == True, SalesOrder.so_type_desc.isnot(None))  # noqa: E712
        .distinct()
        .all()
    )
    return sorted(r.so_type_desc for r in rows if r.so_type_desc)


def get_customer_groups() -> list[str]:
    """Return sorted list of distinct non-null customer groups from open orders."""
    rows = (
        db.session.query(SalesOrder.customer_group)
        .filter(SalesOrder.open_order == True, SalesOrder.customer_group.isnot(None))  # noqa: E712
        .distinct()
        .all()
    )
    return sorted(r.customer_group for r in rows if r.customer_group)


def get_countries() -> list[str]:
    """Return sorted list of distinct non-null countries from open orders."""
    rows = (
        db.session.query(SalesOrder.customer_country)
        .filter(SalesOrder.open_order == True, SalesOrder.customer_country.isnot(None))  # noqa: E712
        .distinct()
        .all()
    )
    return sorted(r.customer_country for r in rows if r.customer_country)


# ---------------------------------------------------------------------------
# Sales Order Comments
# ---------------------------------------------------------------------------

def get_comment_summaries(so_numbers: list[str]) -> dict[str, dict]:
    """
    Return comment summary per SO:
        {so_number: {count, latest_body, latest_user, latest_at}}
    """
    if not so_numbers:
        return {}

    from sqlalchemy.orm import aliased
    from app.auth.models import User

    count_rows = (
        db.session.query(SalesOrderComment.so_number, func.count(SalesOrderComment.id))
        .filter(SalesOrderComment.so_number.in_(so_numbers))
        .group_by(SalesOrderComment.so_number)
        .all()
    )
    counts = {so: cnt for so, cnt in count_rows}

    max_id_sub = (
        db.session.query(
            SalesOrderComment.so_number,
            func.max(SalesOrderComment.id).label("max_id"),
        )
        .filter(SalesOrderComment.so_number.in_(so_numbers))
        .group_by(SalesOrderComment.so_number)
        .subquery()
    )
    UserAlias = aliased(User)
    latest_rows = (
        db.session.query(SalesOrderComment, UserAlias.username)
        .join(max_id_sub, SalesOrderComment.id == max_id_sub.c.max_id)
        .outerjoin(UserAlias, SalesOrderComment.user_id == UserAlias.id)
        .all()
    )

    result = {
        so: {"count": cnt, "latest_body": None, "latest_user": None, "latest_at": None}
        for so, cnt in counts.items()
    }
    for comment, username in latest_rows:
        result[comment.so_number].update({
            "latest_body": comment.body,
            "latest_user": username or "deleted",
            "latest_at":   comment.created_at.strftime("%d %b %H:%M"),
        })
    return result


def get_so_comments(so_number: str) -> list[SalesOrderComment]:
    """Return all comments for an SO, oldest first."""
    return (
        SalesOrderComment.query
        .filter_by(so_number=so_number)
        .order_by(SalesOrderComment.created_at.asc())
        .all()
    )


def add_so_comment(so_number: str, user_id: int, body: str) -> SalesOrderComment:
    """Append a new comment to an SO. Raises ValidationError if body is blank."""
    body = body.strip()
    if not body:
        raise ValidationError("Comment cannot be blank.")
    comment = SalesOrderComment(so_number=so_number, user_id=user_id, body=body)
    db.session.add(comment)
    db.session.commit()
    return comment


# ---------------------------------------------------------------------------
# Customer list
# ---------------------------------------------------------------------------

def get_customer_list() -> list[dict]:
    """Return one row per distinct customer_id, ordered by customer_name."""
    rows = (
        db.session.query(
            SalesOrder.customer_id,
            func.max(SalesOrder.customer_name).label("customer_name"),
        )
        .filter(SalesOrder.customer_id.isnot(None))
        .group_by(SalesOrder.customer_id)
        .order_by(func.max(SalesOrder.customer_name))
        .all()
    )
    return [
        {"customer_id": r.customer_id, "customer_name": r.customer_name or r.customer_id}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Customer report
# ---------------------------------------------------------------------------

def get_customer_report(customer_ids: list[str], closed_months: int = 12) -> Optional[dict]:
    """
    Return all data needed for the customer report page.

    Accepts one or more customer_ids so that accounts representing the same
    real customer can be viewed in a single combined report.
    Returns None if no records exist for the given customer_ids.
    """
    today       = date.today()
    this_monday = today - timedelta(days=today.weekday())

    # ── Customer info (supports combined view for multiple accounts) ──────────
    selected_rows = (
        db.session.query(
            SalesOrder.customer_id,
            func.max(SalesOrder.customer_name).label("customer_name"),
            func.max(SalesOrder.customer_country).label("customer_country"),
            func.max(SalesOrder.customer_group).label("customer_group"),
            func.max(SalesOrder.channel).label("channel"),
        )
        .filter(SalesOrder.customer_id.in_(customer_ids))
        .group_by(SalesOrder.customer_id)
        .order_by(func.max(SalesOrder.customer_name))
        .all()
    )
    if not selected_rows:
        return None

    selected_customers = [
        {"customer_id": r.customer_id, "customer_name": r.customer_name or r.customer_id}
        for r in selected_rows
    ]
    is_combined = len(selected_rows) > 1

    if not is_combined:
        r0 = selected_rows[0]
        customer_info = {
            "customer_id":      r0.customer_id or "",
            "customer_name":    r0.customer_name or "",
            "customer_country": r0.customer_country or "",
            "customer_group":   r0.customer_group or "",
            "channel":          r0.channel or "",
            "is_combined":      False,
        }
    else:
        countries = {r.customer_country for r in selected_rows if r.customer_country}
        groups    = {r.customer_group    for r in selected_rows if r.customer_group}
        channels  = {r.channel           for r in selected_rows if r.channel}
        customer_info = {
            "customer_id":      " / ".join(r.customer_id for r in selected_rows),
            "customer_name":    f"{len(selected_rows)} accounts combined",
            "customer_country": next(iter(countries)) if len(countries) == 1 else "",
            "customer_group":   next(iter(groups))    if len(groups)    == 1 else "",
            "channel":          next(iter(channels))  if len(channels)  == 1 else "",
            "is_combined":      True,
        }

    # ── Open orders (deduped by release, grouped by order_num) ───────────────
    dedup_sub = _so_dedup_subq(open_only=True)

    all_open_rows = (
        db.session.query(SalesOrder)
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(SalesOrder.customer_id.in_(customer_ids))
        .order_by(SalesOrder.need_by_date.asc().nullslast(), SalesOrder.order_num)
        .all()
    )

    seen: dict[int, dict] = {}
    open_orders: list[dict] = []
    for so in all_open_rows:
        if so.order_num not in seen:
            entry: dict = {
                "so_number":          str(so.order_num),
                "customer_name":      so.customer_name or "",
                "customer_order_ref": so.po_num,
                "order_type":         so.so_type_desc or "",
                "so_type":            so.so_type or "",
                "order_date":         so.order_date,
                "_releases":          [],
            }
            seen[so.order_num] = entry
            open_orders.append(entry)
        seen[so.order_num]["_releases"].append(so)

    for entry in open_orders:
        releases = entry.pop("_releases")
        due_dates = [r.need_by_date for r in releases if r.need_by_date]
        entry["due_date"]    = min(due_dates) if due_dates else None
        entry["days_delta"]  = (entry["due_date"] - today).days if entry["due_date"] else None
        entry["total_qty"]   = sum(float(r.selling_qty or 0) for r in releases)
        entry["total_value"] = sum(float(r.release_price_gbp or 0) for r in releases)
        entry["line_count"]  = len({r.order_line for r in releases})

    # ── Open summary KPIs ─────────────────────────────────────────────────────
    open_units = float(
        db.session.query(func.sum(_unit_qty_so))
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(SalesOrder.customer_id.in_(customer_ids))
        .scalar() or 0.0
    )
    overdue_list = [o for o in open_orders if o["due_date"] and o["due_date"] < today]

    open_summary = {
        "open_orders":    len(open_orders),
        "overdue_orders": len(overdue_list),
        "open_value":     sum(o["total_value"] for o in open_orders),
        "overdue_value":  sum(o["total_value"] for o in overdue_list),
        "open_units":     open_units,
    }

    # ── Forward demand by week (next 12 weeks + overdue bucket) ──────────────
    week_labels: list[str] = ["Overdue"]
    week_values: list[float] = [0.0]
    week_units_list: list[float] = [0.0]
    week_starts: list = [None]
    for i in range(12):
        ws = this_monday + timedelta(weeks=i)
        week_labels.append(f"Wk {ws.isocalendar()[1]}")
        week_values.append(0.0)
        week_units_list.append(0.0)
        week_starts.append(ws)

    for o in open_orders:
        if not o["due_date"]:
            continue
        if o["due_date"] < this_monday:
            bucket = 0
        else:
            bucket = next(
                (i for i, ws in enumerate(week_starts[1:], 1)
                 if ws <= o["due_date"] <= ws + timedelta(days=6)),
                None,
            )
        if bucket is not None:
            week_values[bucket]      += o["total_value"]
            week_units_list[bucket]  += o["total_qty"]

    # ── Order mix: by product group and by model ──────────────────────────────
    by_product_group_rows = (
        db.session.query(
            SalesOrder.prod_code,
            func.sum(_unit_qty_so).label("cnt"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.prod_code.isnot(None),
        )
        .group_by(SalesOrder.prod_code)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .all()
    )

    by_model_rows = (
        db.session.query(
            SalesOrder.model,
            func.sum(_unit_qty_so).label("cnt"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.model.isnot(None),
            SalesOrder.model != "",
            SalesOrder.model != "Scatter",
        )
        .group_by(SalesOrder.model)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .limit(15)
        .all()
    )

    # ── Closed orders (last closed_months months, grouped by order_num) ───────
    closed_from = today - timedelta(days=30 * closed_months)

    closed_rows = (
        db.session.query(
            SalesOrder.order_num,
            SalesOrder.po_num,
            SalesOrder.order_date,
            SalesOrder.so_type_desc,
            func.max(SalesOrder.customer_name).label("customer_name"),
            func.min(SalesOrder.need_by_date).label("min_due"),
            func.sum(SalesOrder.release_price_gbp).label("total_value"),
            func.sum(SalesOrder.selling_qty).label("total_qty"),
            func.count(SalesOrder.order_line.distinct()).label("line_count"),
        )
        .filter(
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.open_order == False,  # noqa: E712
            SalesOrder.order_date >= closed_from,
        )
        .group_by(
            SalesOrder.order_num,
            SalesOrder.po_num,
            SalesOrder.order_date,
            SalesOrder.so_type_desc,
        )
        .order_by(SalesOrder.order_date.desc())
        .all()
    )

    closed_orders = [
        {
            "so_number":          str(r.order_num),
            "customer_name":      r.customer_name or "",
            "customer_order_ref": r.po_num,
            "order_date":         r.order_date,
            "order_type":         r.so_type_desc or "",
            "due_date":           r.min_due,
            "total_qty":          float(r.total_qty or 0),
            "total_value":        float(r.total_value or 0),
            "line_count":         r.line_count,
        }
        for r in closed_rows
    ]

    closed_summary = {
        "closed_orders": len(closed_orders),
        "closed_value":  sum(o["total_value"] for o in closed_orders),
        "closed_units":  sum(o["total_qty"] for o in closed_orders),
    }

    # ── Monthly intake trend (last 12 months of closed orders, by month) ──────
    intake_from = today - timedelta(days=365)
    monthly_rows = (
        db.session.query(
            func.strftime("%Y-%m", SalesOrder.order_date).label("ym"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
            func.sum(SalesOrder.selling_qty).label("qty"),
            func.count(SalesOrder.order_num.distinct()).label("cnt"),
        )
        .filter(
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.open_order == False,  # noqa: E712
            SalesOrder.order_date >= intake_from,
            SalesOrder.order_date.isnot(None),
        )
        .group_by(func.strftime("%Y-%m", SalesOrder.order_date))
        .order_by(func.strftime("%Y-%m", SalesOrder.order_date))
        .all()
    )
    monthly_intake = [
        {
            "month":  r.ym,
            "value":  round(float(r.val or 0), 2),
            "units":  round(float(r.qty or 0), 0),
            "orders": r.cnt,
        }
        for r in monthly_rows
    ]

    # ── Overdue age breakdown ─────────────────────────────────────────────────
    age_labels = ["1–7 days", "8–14 days", "15–30 days", "31–60 days", "60+ days"]
    age_counts = [0, 0, 0, 0, 0]
    for o in overdue_list:
        d = abs(o["days_delta"])
        if   d <= 7:  age_counts[0] += 1
        elif d <= 14: age_counts[1] += 1
        elif d <= 30: age_counts[2] += 1
        elif d <= 60: age_counts[3] += 1
        else:         age_counts[4] += 1
    overdue_age_chart = {"labels": age_labels, "counts": age_counts}

    # ── Top products from closed history ─────────────────────────────────────
    top_products_rows = (
        db.session.query(
            SalesOrder.part_num,
            SalesOrder.part_desc,
            func.sum(SalesOrder.selling_qty).label("qty"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
            func.count(SalesOrder.order_num.distinct()).label("cnt"),
        )
        .filter(
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.open_order == False,  # noqa: E712
            SalesOrder.order_date >= closed_from,
            SalesOrder.part_num.isnot(None),
            SalesOrder.part_num != "",
        )
        .group_by(SalesOrder.part_num, SalesOrder.part_desc)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .limit(10)
        .all()
    )
    top_products_closed = [
        {
            "part_num":  r.part_num,
            "part_desc": r.part_desc or r.part_num,
            "units":     round(float(r.qty or 0), 0),
            "value":     round(float(r.val or 0), 2),
            "orders":    r.cnt,
        }
        for r in top_products_rows
    ]

    # ── Lead time distribution (order_date → need_by_date, all orders) ────────
    lt_rows = (
        db.session.query(
            SalesOrder.order_date,
            SalesOrder.need_by_date,
        )
        .filter(
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.order_date.isnot(None),
            SalesOrder.need_by_date.isnot(None),
        )
        .distinct()
        .all()
    )
    lt_labels = ["<2 wks", "2–4 wks", "4–8 wks", "8–12 wks", "12–24 wks", "24 wks+"]
    lt_counts = [0, 0, 0, 0, 0, 0]
    for r in lt_rows:
        days = (r.need_by_date - r.order_date).days
        if   days <  14: lt_counts[0] += 1
        elif days <  28: lt_counts[1] += 1
        elif days <  56: lt_counts[2] += 1
        elif days <  84: lt_counts[3] += 1
        elif days < 168: lt_counts[4] += 1
        else:            lt_counts[5] += 1
    lead_time_dist = {"labels": lt_labels, "counts": lt_counts}

    # ── Order type breakdown (open orders) ─────────────────────────────────────────
    by_order_type_rows = (
        db.session.query(
            SalesOrder.so_type_desc,
            func.sum(_unit_qty_so).label("cnt"),
            func.sum(SalesOrder.release_price_gbp).label("val"),
            func.count(SalesOrder.order_num.distinct()).label("orders"),
        )
        .join(dedup_sub, SalesOrder.id == dedup_sub.c.id)
        .filter(
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.so_type_desc.isnot(None),
        )
        .group_by(SalesOrder.so_type_desc)
        .order_by(func.sum(SalesOrder.release_price_gbp).desc())
        .all()
    )

    return {
        "customer_info":    customer_info,
        "selected_customers": selected_customers,
        "open_summary":    open_summary,
        "open_orders":     open_orders,
        "weekly_schedule": {
            "labels": week_labels,
            "values": [round(v, 2) for v in week_values],
            "units":  [round(v, 0) for v in week_units_list],
        },
        "by_product_group": [
            {"group": r.prod_code, "units": float(r.cnt or 0), "value": float(r.val or 0)}
            for r in by_product_group_rows
        ],
        "by_model": [
            {"model": r.model, "units": float(r.cnt or 0), "value": float(r.val or 0)}
            for r in by_model_rows
        ],
        "by_order_type": [
            {"order_type": r.so_type_desc, "units": float(r.cnt or 0),
             "value": float(r.val or 0), "orders": r.orders}
            for r in by_order_type_rows
        ],
        "closed_orders":       closed_orders,
        "closed_summary":      closed_summary,
        "monthly_intake":      monthly_intake,
        "overdue_age_chart":   overdue_age_chart,
        "top_products_closed": top_products_closed,
        "lead_time_dist":      lead_time_dist,
    }
