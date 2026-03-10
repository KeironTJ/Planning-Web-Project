"""
Materials service layer.

Shortage calculation:
  shortage = net_requirement - stock_on_hand - sum(open PO qty due <= requirement due_date)

Where:
  net_requirement (main)       = qty_for_order - qty_issued
  net_requirement (after sales) = qty_required
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_stock() -> dict[str, Decimal]:
    """Return {product_code: qty_on_hand} for all stock rows."""
    rows = db.session.query(Stock.product_code, Stock.qty_on_hand).all()
    return {r.product_code: (r.qty_on_hand or Decimal(0)) for r in rows}


def _load_po_coverage() -> dict[str, list[tuple[date, Decimal]]]:
    """
    Return {product_code: [(due_date, outstanding_qty), ...]} sorted by due_date asc.
    Used to compute cumulative PO coverage up to any given due date.
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
        )
        .order_by(PurchaseOrder.product_code, PurchaseOrder.due_date)
        .all()
    )
    coverage: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for r in rows:
        if r.due_date is not None:
            coverage[r.product_code].append((r.due_date, r.outstanding_qty or Decimal(0)))
    return dict(coverage)


def _po_coverage_for(
    po_map: dict[str, list[tuple[date, Decimal]]],
    product_code: str,
    req_due_date: Optional[date],
) -> Decimal:
    """Sum PO outstanding_qty where po.due_date <= req_due_date."""
    if req_due_date is None:
        return Decimal(0)
    lines = po_map.get(product_code, [])
    return sum(
        (qty for d, qty in lines if d <= req_due_date),
        Decimal(0),
    )


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
    Compute material shortages and return structured data for the template.

    Returns:
        {
            "rows": [ShortageRow, ...],
            "total_rows": int,
            "shortage_count": int,
            "summary": {source: {"count": int, "rows": int}},
            "stock_imported": bool,
            "reqs_imported": bool,
        }
    """
    stock_map  = _load_stock()
    po_map     = _load_po_coverage()

    rows: list[ShortageRow] = []

    # ---- Main line requirements ----
    if source in ("all", "main"):
        q = MaterialRequirementMain.query.filter(
            MaterialRequirementMain.complete != "Y"
        )
        if dept_filter:
            q = q.filter(MaterialRequirementMain.department == dept_filter)
        if due_before:
            q = q.filter(MaterialRequirementMain.due_date <= due_before)
        if search:
            term = f"%{search.strip()}%"
            q = q.filter(
                db.or_(
                    MaterialRequirementMain.material_code.ilike(term),
                    MaterialRequirementMain.material_description.ilike(term),
                    MaterialRequirementMain.works_order.ilike(term),
                )
            )

        for req in q.order_by(MaterialRequirementMain.due_date).all():
            mc          = req.material_code or ""
            qty_req     = req.qty_for_order or Decimal(0)
            qty_issued  = req.qty_issued or Decimal(0)
            net_req     = max(Decimal(0), qty_req - qty_issued)
            stock       = stock_map.get(mc, Decimal(0))
            po_cov      = _po_coverage_for(po_map, mc, req.due_date)
            shortage    = max(Decimal(0), net_req - stock - po_cov)

            if shortages_only and shortage == 0:
                continue

            rows.append(ShortageRow(
                source="main",
                material_code=mc,
                description=req.material_description or "",
                department=req.department or "",
                due_date=req.due_date,
                qty_required=qty_req,
                qty_issued=qty_issued,
                net_required=net_req,
                stock_on_hand=stock,
                po_coverage=po_cov,
                shortage=shortage,
                works_order=req.works_order,
                customer_id=req.customer_id,
                complete=req.complete,
            ))

    # ---- After sales requirements ----
    if source in ("all", "aftersales"):
        q = MaterialRequirementAfterSales.query
        if dept_filter:
            q = q.filter(MaterialRequirementAfterSales.department == dept_filter)
        if due_before:
            q = q.filter(MaterialRequirementAfterSales.due_date <= due_before)
        if search:
            term = f"%{search.strip()}%"
            q = q.filter(
                db.or_(
                    MaterialRequirementAfterSales.product_code.ilike(term),
                    MaterialRequirementAfterSales.description.ilike(term),
                    MaterialRequirementAfterSales.order_number.ilike(term),
                )
            )

        for req in q.order_by(MaterialRequirementAfterSales.due_date).all():
            pc       = req.product_code or ""
            net_req  = req.qty_required or Decimal(0)
            stock    = stock_map.get(pc, Decimal(0))
            po_cov   = _po_coverage_for(po_map, pc, req.due_date)
            shortage = max(Decimal(0), net_req - stock - po_cov)

            if shortages_only and shortage == 0:
                continue

            rows.append(ShortageRow(
                source="aftersales",
                material_code=pc,
                description=req.description or "",
                department=req.department or "",
                due_date=req.due_date,
                qty_required=net_req,
                qty_issued=Decimal(0),
                net_required=net_req,
                stock_on_hand=stock,
                po_coverage=po_cov,
                shortage=shortage,
                order_number=req.order_number,
                customer=req.customer,
            ))

    # Sort all rows by due_date then shortage desc
    rows.sort(key=lambda r: (r.due_date or date.max, -r.shortage))

    shortage_count = sum(1 for r in rows if r.shortage > 0)

    return {
        "rows":          rows,
        "total_rows":    len(rows),
        "shortage_count": shortage_count,
        "stock_imported": bool(stock_map),
        "reqs_imported":  bool(rows) or _has_reqs(source),
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
