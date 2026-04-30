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
from .models import SalesOrderLine, SalesOrderComment

# Treat is_open = NULL as open (backwards-compatible before re-import sets the flag)
_OPEN = db.or_(SalesOrderLine.is_open == True, SalesOrderLine.is_open.is_(None))  # noqa: E712

# Units = qty_ordered for non-Scatter, non-blank model lines only
_unit_qty = case(
    (db.and_(SalesOrderLine.model.isnot(None), SalesOrderLine.model != "Scatter"),
     SalesOrderLine.qty_ordered),
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
    Return open sales order lines grouped by SO number.

    Each item in the returned list is a dict:
        so_number           str
        customer_code       str
        customer_name       str
        customer_order_ref  str | None
        order_type          str
        country             str
        customer_group      str
        channel             str
        order_date          date | None
        due_date            date | None   - earliest due date across lines
        days_delta          int | None    - days until/past due (negative = overdue)
        total_qty           float         - sum of qty_ordered across lines
        total_value         float         - sum of total_value across lines
        line_count          int
        lines               list[SalesOrderLine]
    """
    today = date.today()

    q = SalesOrderLine.query.filter(_OPEN)

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(db.or_(
            SalesOrderLine.so_number.ilike(term),
            SalesOrderLine.customer_name.ilike(term),
            SalesOrderLine.product_code.ilike(term),
            SalesOrderLine.product_description.ilike(term),
            SalesOrderLine.customer_order_ref.ilike(term),
        ))

    if order_type_filter:
        q = q.filter(SalesOrderLine.order_type == order_type_filter)

    if customer_filter:
        q = q.filter(SalesOrderLine.customer_name.ilike(f"%{customer_filter.strip()}%"))

    if customer_po_filter:
        q = q.filter(SalesOrderLine.customer_order_ref.ilike(f"%{customer_po_filter.strip()}%"))

    if country_filter:
        q = q.filter(SalesOrderLine.country == country_filter)

    if customer_group_filter:
        q = q.filter(SalesOrderLine.customer_group == customer_group_filter)

    if overdue_only:
        q = q.filter(
            SalesOrderLine.due_date < today,
            SalesOrderLine.due_date.isnot(None),
        )

    if due_date_from:
        q = q.filter(SalesOrderLine.due_date >= due_date_from)
    if due_date_to:
        q = q.filter(SalesOrderLine.due_date <= due_date_to)

    if order_by == "so_number":
        q = q.order_by(SalesOrderLine.so_number, SalesOrderLine.line_number)
    elif order_by == "customer":
        q = q.order_by(
            SalesOrderLine.customer_name,
            SalesOrderLine.due_date.asc().nullslast(),
            SalesOrderLine.so_number,
        )
    else:  # due_date (default) or value — value is post-sorted in Python
        q = q.order_by(
            SalesOrderLine.due_date.asc().nullslast(),
            SalesOrderLine.so_number,
            SalesOrderLine.line_number,
        )

    all_lines = q.all()

    # Group by SO number maintaining query order
    seen: dict[str, dict] = {}
    order_list: list[dict] = []
    for sol in all_lines:
        if sol.so_number not in seen:
            entry = {
                "so_number":     sol.so_number,
                "customer_code": sol.customer_code or "",
                "customer_name": sol.customer_name or "",
                "lines":         [],
            }
            seen[sol.so_number] = entry
            order_list.append(entry)
        seen[sol.so_number]["lines"].append(sol)

    for entry in order_list:
        lines = entry["lines"]
        due_dates = [s.due_date for s in lines if s.due_date]
        entry["due_date"]           = min(due_dates) if due_dates else None
        entry["days_delta"]         = (entry["due_date"] - today).days if entry["due_date"] else None
        entry["total_qty"]          = sum(float(s.qty_ordered or 0) for s in lines)
        entry["total_value"]        = sum(float(s.total_value or 0) for s in lines)
        entry["line_count"]         = len(lines)
        entry["order_type"]         = lines[0].order_type or "" if lines else ""
        entry["customer_order_ref"] = lines[0].customer_order_ref if lines else None
        entry["country"]            = lines[0].country or "" if lines else ""
        entry["customer_group"]     = lines[0].customer_group or "" if lines else ""
        entry["channel"]            = lines[0].channel or "" if lines else ""
        entry["order_date"]         = lines[0].order_date if lines else None

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
        total        - distinct SO count
        overdue      - distinct SOs with a past due_date
        total_value  - sum of all line values
    """
    today = date.today()

    total = db.session.query(
        func.count(SalesOrderLine.so_number.distinct())
    ).filter(_OPEN).scalar() or 0

    overdue = db.session.query(
        func.count(SalesOrderLine.so_number.distinct())
    ).filter(
        _OPEN,
        SalesOrderLine.due_date < today,
        SalesOrderLine.due_date.isnot(None),
    ).scalar() or 0

    total_value = db.session.query(
        func.sum(SalesOrderLine.total_value)
    ).filter(_OPEN).scalar() or 0.0

    overdue_value = db.session.query(
        func.sum(SalesOrderLine.total_value)
    ).filter(
        _OPEN,
        SalesOrderLine.due_date < today,
        SalesOrderLine.due_date.isnot(None),
    ).scalar() or 0.0

    total_units_open = db.session.query(
        func.sum(_unit_qty)
    ).filter(_OPEN).scalar() or 0.0

    return {
        "total":         total,
        "overdue":       overdue,
        "total_value":   float(total_value),
        "overdue_value": float(overdue_value),
        "total_units":   float(total_units_open),
    }


# ---------------------------------------------------------------------------
# Dashboard data
# ---------------------------------------------------------------------------

def get_order_book_dashboard() -> dict:
    """
    Return aggregated data for the orders dashboard charts and KPIs.

    Keys:
        summary           - {total, overdue, total_value}
        due_by_week       - {labels, counts, values}
        value_by_customer - top 15 [{customer, value, count}]
        by_product_group  - [{group, count, value}]
        by_model          - top 15 [{model, count, value}]
        by_customer_group - [{group, count, value}]
        by_channel        - [{channel, count, value}]
    """
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())

    summary = get_order_book_summary()

    # Orders due by week (overdue bucket + next 10 weeks)
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

    open_filter = _OPEN

    so_due_rows = (
        db.session.query(
            SalesOrderLine.so_number,
            func.min(SalesOrderLine.due_date).label("min_due"),
            func.sum(SalesOrderLine.total_value).label("total_val"),
            func.sum(_unit_qty).label("total_qty"),
        )
        .filter(open_filter)
        .group_by(SalesOrderLine.so_number)
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

    # Value by customer (top 15) — open orders only
    value_by_customer_rows = (
        db.session.query(
            SalesOrderLine.customer_name,
            func.sum(SalesOrderLine.total_value).label("val"),
            func.sum(_unit_qty).label("cnt"),
        )
        .filter(open_filter, SalesOrderLine.customer_name.isnot(None))
        .group_by(SalesOrderLine.customer_name)
        .order_by(func.sum(SalesOrderLine.total_value).desc())
        .limit(15)
        .all()
    )

    # By product group — open orders only
    by_product_group_rows = (
        db.session.query(
            SalesOrderLine.product_group,
            func.sum(_unit_qty).label("cnt"),
            func.sum(SalesOrderLine.total_value).label("val"),
        )
        .filter(open_filter, SalesOrderLine.product_group.isnot(None))
        .group_by(SalesOrderLine.product_group)
        .order_by(func.sum(SalesOrderLine.total_value).desc())
        .all()
    )

    # By model (top 15 by value, excluding Scatter) — open orders only
    by_model_rows = (
        db.session.query(
            SalesOrderLine.model,
            func.count(SalesOrderLine.so_number.distinct()).label("cnt"),
            func.sum(SalesOrderLine.total_value).label("val"),
        )
        .filter(
            open_filter,
            SalesOrderLine.model.isnot(None),
            SalesOrderLine.model != "Scatter",
        )
        .group_by(SalesOrderLine.model)
        .order_by(func.sum(SalesOrderLine.total_value).desc())
        .limit(15)
        .all()
    )

    # By customer group — open orders only
    by_customer_group_rows = (
        db.session.query(
            SalesOrderLine.customer_group,
            func.sum(_unit_qty).label("cnt"),
            func.sum(SalesOrderLine.total_value).label("val"),
        )
        .filter(open_filter, SalesOrderLine.customer_group.isnot(None))
        .group_by(SalesOrderLine.customer_group)
        .order_by(func.sum(SalesOrderLine.total_value).desc())
        .all()
    )

    # By channel (IC Description) — open orders only
    by_channel_rows = (
        db.session.query(
            SalesOrderLine.channel,
            func.sum(_unit_qty).label("cnt"),
            func.sum(SalesOrderLine.total_value).label("val"),
        )
        .filter(open_filter, SalesOrderLine.channel.isnot(None))
        .group_by(SalesOrderLine.channel)
        .order_by(func.sum(SalesOrderLine.total_value).desc())
        .all()
    )

    # By country (top 15) — open orders only
    by_country_rows = (
        db.session.query(
            SalesOrderLine.country,
            func.sum(_unit_qty).label("cnt"),
            func.sum(SalesOrderLine.total_value).label("val"),
        )
        .filter(open_filter, SalesOrderLine.country.isnot(None))
        .group_by(SalesOrderLine.country)
        .order_by(func.sum(SalesOrderLine.total_value).desc())
        .limit(15)
        .all()
    )

    # By order type — open orders only
    by_order_type_rows = (
        db.session.query(
            SalesOrderLine.order_type,
            func.sum(_unit_qty).label("cnt"),
            func.sum(SalesOrderLine.total_value).label("val"),
        )
        .filter(open_filter, SalesOrderLine.order_type.isnot(None))
        .group_by(SalesOrderLine.order_type)
        .order_by(func.sum(SalesOrderLine.total_value).desc())
        .all()
    )

    # Lead time distribution — days between order_date and due_date for open orders
    lt_rows = (
        db.session.query(
            SalesOrderLine.order_date,
            SalesOrderLine.due_date,
        )
        .filter(
            open_filter,
            SalesOrderLine.order_date.isnot(None),
            SalesOrderLine.due_date.isnot(None),
        )
        .all()
    )
    lt_bucket_labels = ["<2 wks", "2–4 wks", "4–8 wks", "8–12 wks", "12–24 wks", "24 wks+"]
    lt_bucket_counts = [0, 0, 0, 0, 0, 0]
    for r in lt_rows:
        days = (r.due_date - r.order_date).days
        if   days <  14: lt_bucket_counts[0] += 1
        elif days <  28: lt_bucket_counts[1] += 1
        elif days <  56: lt_bucket_counts[2] += 1
        elif days <  84: lt_bucket_counts[3] += 1
        elif days < 168: lt_bucket_counts[4] += 1
        else:            lt_bucket_counts[5] += 1

    # Next 4-week horizon grid — open units due per model per week
    horizon_weeks = [this_monday + timedelta(weeks=i) for i in range(4)]
    horizon_labels = [f"Wk {ws.isocalendar()[1]}" for ws in horizon_weeks]
    horizon_raw = (
        db.session.query(
            SalesOrderLine.model,
            SalesOrderLine.due_date,
            SalesOrderLine.qty_ordered,
        )
        .filter(
            open_filter,
            SalesOrderLine.model.isnot(None),
            SalesOrderLine.model != "Scatter",
            SalesOrderLine.due_date >= this_monday,
            SalesOrderLine.due_date < this_monday + timedelta(weeks=4),
        )
        .all()
    )
    horizon_by_model: dict[str, list] = {}
    for r in horizon_raw:
        if not r.due_date or not r.model:
            continue
        m = r.model
        if m not in horizon_by_model:
            horizon_by_model[m] = [0.0, 0.0, 0.0, 0.0]
        for i, ws in enumerate(horizon_weeks):
            if ws <= r.due_date <= ws + timedelta(days=6):
                horizon_by_model[m][i] += float(r.qty_ordered or 0)
                break
    horizon_rows_sorted = sorted(
        [{"model": m, "weeks": wks, "total": sum(wks)}
         for m, wks in horizon_by_model.items()],
        key=lambda x: x["total"], reverse=True
    )[:15]

    # Order intake over time — ALL non-void orders grouped by year + month
    current_year = date.today().year
    intake_start = date(current_year - 3, 1, 1)  # last 4 calendar years
    intake_raw = (
        db.session.query(
            func.strftime("%Y", SalesOrderLine.order_date).label("yr"),
            func.strftime("%m", SalesOrderLine.order_date).label("mn"),
            func.sum(SalesOrderLine.total_value).label("val"),
            func.sum(_unit_qty).label("qty"),
        )
        .filter(
            SalesOrderLine.order_date.isnot(None),
            SalesOrderLine.order_date >= intake_start,
        )
        .group_by(
            func.strftime("%Y", SalesOrderLine.order_date),
            func.strftime("%m", SalesOrderLine.order_date),
        )
        .order_by(
            func.strftime("%Y", SalesOrderLine.order_date),
            func.strftime("%m", SalesOrderLine.order_date),
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
            {"group": r.product_group, "units": float(r.cnt or 0), "value": float(r.val or 0)}
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
            {"country": r.country, "units": float(r.cnt or 0), "value": float(r.val or 0)}
            for r in by_country_rows
        ],
        "by_order_type": [
            {"order_type": r.order_type, "units": float(r.cnt or 0), "value": float(r.val or 0)}
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
    Return overdue orders for the overdue report view.
    An order is overdue when its earliest due_date is in the past.
    """
    today = date.today()

    q = (
        db.session.query(
            SalesOrderLine.so_number,
            SalesOrderLine.customer_name,
            SalesOrderLine.order_type,
            func.min(SalesOrderLine.due_date).label("min_due"),
            func.sum(SalesOrderLine.total_value).label("total_value"),
            func.count(SalesOrderLine.id).label("line_count"),
        )
        .filter(
            _OPEN,
            SalesOrderLine.due_date < today,
            SalesOrderLine.due_date.isnot(None),
        )
        .group_by(
            SalesOrderLine.so_number,
            SalesOrderLine.customer_name,
            SalesOrderLine.order_type,
        )
    )

    if customer_filter:
        q = q.filter(SalesOrderLine.customer_name.ilike(f"%{customer_filter.strip()}%"))

    rows = q.all()

    search_lower = search.lower() if search else None
    records = []
    for row in rows:
        if search_lower:
            if (
                search_lower not in (row.so_number or "").lower()
                and search_lower not in (row.customer_name or "").lower()
            ):
                continue
        days = (today - row.min_due).days if row.min_due else 0
        records.append({
            "so_number":    row.so_number,
            "customer_name": row.customer_name or "-",
            "order_type":   row.order_type,
            "total_value":  float(row.total_value or 0),
            "min_due":      row.min_due,
            "days_overdue": days,
            "line_count":   row.line_count,
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
            SalesOrderLine.customer_name,
            func.sum(SalesOrderLine.total_value).label("val"),
            func.count(SalesOrderLine.so_number.distinct()).label("cnt"),
        )
        .filter(
            SalesOrderLine.due_date < today,
            SalesOrderLine.due_date.isnot(None),
            SalesOrderLine.customer_name.isnot(None),
        )
        .group_by(SalesOrderLine.customer_name)
        .order_by(func.sum(SalesOrderLine.total_value).desc())
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
    """Return sorted list of distinct non-null order types from sales order lines."""
    rows = (
        db.session.query(SalesOrderLine.order_type)
        .filter(SalesOrderLine.order_type.isnot(None))
        .distinct()
        .all()
    )
    return sorted(r.order_type for r in rows if r.order_type)


def get_customer_groups() -> list[str]:
    """Return sorted list of distinct non-null customer groups."""
    rows = (
        db.session.query(SalesOrderLine.customer_group)
        .filter(SalesOrderLine.customer_group.isnot(None))
        .distinct()
        .all()
    )
    return sorted(r.customer_group for r in rows if r.customer_group)


def get_countries() -> list[str]:
    """Return sorted list of distinct non-null countries."""
    rows = (
        db.session.query(SalesOrderLine.country)
        .filter(SalesOrderLine.country.isnot(None))
        .distinct()
        .all()
    )
    return sorted(r.country for r in rows if r.country)


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
