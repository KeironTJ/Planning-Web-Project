"""
DB loader helpers — thin read-only queries that feed the MRP engine.

All functions return plain Python dicts/sets so callers never hold open
ORM sessions longer than needed, and mocking for tests is straightforward.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from ..models import MrpExemptMaterial, PurchaseOrder, Stock


def _load_exempt_codes() -> frozenset[str]:
    """Return the set of material codes exempt from MRP shortage calculations."""
    rows = db.session.query(MrpExemptMaterial.material_code).all()
    return frozenset(r.material_code for r in rows)


def _load_stock() -> dict[str, Decimal]:
    """
    Return {part_num: total_qty_on_hand} summed across all plants.

    Multi-plant parts (e.g. fabric stored in STORES + PROD) are not
    understated when only the last plant row would otherwise be kept.
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
    Return {part_num: [(effective_date, outstanding_qty), ...]} for open PO releases.

    Overdue POs (due_date < today) are clamped to today — still outstanding
    but assumed to arrive at the earliest now.
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
    from app.admin.models import (
        SystemSetting,
        SETTING_MRP_COMPONENT_LEAD_DAYS,
        SETTING_MRP_LEAD_DAYS,
    )
    key = SETTING_MRP_LEAD_DAYS if group == "fabric" else SETTING_MRP_COMPONENT_LEAD_DAYS
    return SystemSetting.get_int(key, default=14)


def _get_group_class_ids(group: str) -> frozenset[str] | None:
    """
    Return the configured class-ID filter for the given material group.

    Returns None when no filter is set (all classes in the group are included).
    """
    from app.admin.models import (
        SystemSetting,
        SETTING_COMPONENT_CLASS_IDS,
        SETTING_FABRIC_CLASS_IDS,
    )
    if group == "fabric":
        raw = SystemSetting.get(SETTING_FABRIC_CLASS_IDS, "A101,A102,A105,B101,C101,Z102")
    else:
        raw = SystemSetting.get(SETTING_COMPONENT_CLASS_IDS, "")
    raw = raw.strip()
    if not raw:
        return None
    return frozenset(c.strip() for c in raw.split(",") if c.strip())


def _load_co_qty() -> dict[str, Decimal]:
    # OSPurchaseOrders BAQ has no PO-type flag; CO pool unsupported until BAQ exposes it.
    return {}
