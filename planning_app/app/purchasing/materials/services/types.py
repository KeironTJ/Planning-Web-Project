from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


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
    works_order: Optional[str] = None
    so_number: Optional[str] = None
    customer_id: Optional[str] = None
    customer: Optional[str] = None
    complete: Optional[str] = None
    class_id: Optional[str] = None
    job_released: Optional[bool] = None
    job_firm: Optional[bool] = None
    po_exists: bool = False
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
    job_released: Optional[bool] = None
    job_firm: Optional[bool] = None
    mat_status: Optional[str] = None     # 5-tier coverage status for requirement rows
    effective_date: Optional[date] = None  # PO: arrival + lead days; Req: due − lead days
    so_number: Optional[str] = None


@dataclass
class StockBreakdown:
    stores: Decimal
    prod_uk: Decimal
    romania: Decimal
    others: Decimal
    total: Decimal

    @property
    def available(self) -> Decimal:
        return self.stores + self.prod_uk + self.romania


@dataclass
class MrpMaterial:
    material_code: str
    description: str
    opening_stock: Decimal
    has_shortage: bool
    events: list
    mat_status: str = "no_data"
    selected_so_status: Optional[str] = None
    stock_breakdown: Optional[StockBreakdown] = None


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
    "ok":        ("Mat. OK",      "success"),
    "low_risk":  ("Soft Risk",    "info"),
    "med_risk":  ("PO Reliant",   "warning"),
    "late_po":   ("Late PO",      "orange"),
    "high_risk": ("Shortage",     "danger"),
    "no_data":   ("\u2014",       "secondary"),
}

#: 3-state work-order status derived from the job_released / job_firm flags.
#: Jobs that are closed or fully issued are excluded from these reports upstream,
#: so only these three states are ever seen here.
WO_STATUS_META: dict[str, tuple[str, str]] = {
    "released":              ("Released",               "success"),
    "unreleased_firm":       ("Unreleased (Firm)",       "warning"),
    "unreleased_engineered": ("Unreleased (Engineered)", "secondary"),
}


def wo_status(job_released: Optional[bool], job_firm: Optional[bool]) -> str:
    """Derive the 3-state work-order status used for filtering and display."""
    if job_released:
        return "released"
    return "unreleased_firm" if job_firm else "unreleased_engineered"


__all__ = [
    "ShortageRow",
    "MrpEvent",
    "StockBreakdown",
    "MrpMaterial",
    "MAT_STATUS_META",
    "WO_STATUS_META",
    "wo_status",
    "_MAT_STATUS_PRIORITY",
]
