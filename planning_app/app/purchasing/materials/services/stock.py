"""
Stock and purchase order query services.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func

from app.extensions import db
from ..models import MaterialRequirementMain, PurchaseOrder, Stock
from ._cache import _cached_group_report, _cached_unfiltered_report

__all__ = ["get_stock_summary", "get_stock_overview", "get_po_list", "get_stock_list"]


def get_stock_summary() -> dict:
    """Return headline stock stats for the materials dashboard."""
    total = db.session.query(func.count(Stock.id)).scalar() or 0
    zero_stock = (
        db.session.query(func.count(Stock.id))
        .filter(Stock.qty_on_hand <= 0)
        .scalar() or 0
    )
    total_po_lines  = db.session.query(func.count(PurchaseOrder.id)).scalar() or 0
    main_req_count  = db.session.query(func.count(MaterialRequirementMain.id)).scalar() or 0

    cached               = _cached_unfiltered_report()
    shortage_estimate    = sum(1 for r in cached["rows"] if r.shortage > 0)

    comp_cached          = _cached_group_report("component")
    comp_shortage_estimate = sum(1 for r in comp_cached["rows"] if r.shortage > 0)

    from app.sales.orders.models import ImportBatch  # lazy — avoids circular at import time
    last_sync = (
        ImportBatch.query
        .filter_by(import_type="epicor_stock", status="success")
        .order_by(ImportBatch.uploaded_at.desc())
        .first()
    )

    return {
        "stock_lines":       total,
        "zero_stock":        zero_stock,
        "po_lines":          total_po_lines,
        "main_reqs":         main_req_count,
        "shortage_est":      shortage_estimate,
        "comp_shortage_est": comp_shortage_estimate,
        "last_sync":         last_sync,
    }


def get_stock_overview() -> dict:
    """
    Return class-level breakdown and summary KPIs for the Stock On Hand page.

    Returns:
        {
            "total_lines": int,
            "zero_stock":  int,
            "in_deficit":  int,
            "classes":     [{"class_id", "count", "deficit_count", "total_qty"}, ...],
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
            "class_id":      r.class_id or "—",
            "count":         r.count,
            "total_qty":     float(r.total_qty or 0),
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


def get_po_list(
    search: Optional[str] = None,
    due_from=None,
    due_before=None,
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


def get_stock_list(
    search: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    class_filter: Optional[str] = None,
):
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
