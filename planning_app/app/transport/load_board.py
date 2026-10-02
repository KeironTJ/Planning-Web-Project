"""Read-only visualisation of the latest transport snapshot."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.sales.orders.models import ImportBatch
from .importer import TransportLoadImporter
from .models import LOAD_STAGES, TransportLoad
from .readiness import populate_readiness, summarise_readiness
from .manifest_history import attach_manifest_fallback


STAGE_META = (
    ("PLANNED", "Planned", "Load being built", "clipboard", "planned"),
    ("SCHEDULED", "Scheduled", "Booked for shipping", "calendar-check", "scheduled"),
    ("PACKED", "Packed", "Packed, awaiting dispatch", "box-seam", "packed"),
    ("SHIPPED", "Shipped", "Marked shipped in Epicor", "truck", "shipped"),
)


def _totals(loads: list[dict], order_synced: bool) -> dict:
    return {
        "count": len(loads),
        "quantity": sum((load["order_qty"] or Decimal(0) for load in loads), Decimal(0)),
        "value": sum((load["order_value"] or Decimal(0) for load in loads), Decimal(0)),
        "missing_value": sum(load["order_value"] is None for load in loads),
        "missing_quantity": sum(load["order_qty"] is None for load in loads),
        "readiness": summarise_readiness(loads, order_synced),
    }


def get_load_board(
    search: str = "",
    route: str = "",
    ship_from: date | None = None,
    ship_to: date | None = None,
    today: date | None = None,
) -> dict:
    """Counts and visuals share the same filters; dates refer to ship date.

    There is no daily throughput or time-in-stage calculation: CSGTransportWB
    supplies current status and scheduled dates, not transition timestamps.
    """
    today = today or date.today()
    search, route = search.strip(), route.strip()
    query = TransportLoad.query
    if search:
        term = f"%{search}%"
        query = query.filter(db.or_(
            TransportLoad.load_id.ilike(term),
            TransportLoad.route.ilike(term),
            TransportLoad.vehicle.ilike(term),
        ))
    if route:
        query = query.filter(TransportLoad.route == route)
    if ship_from:
        query = query.filter(TransportLoad.ship_date >= ship_from)
    if ship_to:
        query = query.filter(TransportLoad.ship_date <= ship_to)

    rows = query.order_by(
        TransportLoad.ship_date.asc().nullslast(), TransportLoad.load_id,
        TransportLoad.source_row_id,
    ).all()
    loads = []
    for row in rows:
        used_pct = None
        if row.capacity is not None and row.capacity > 0 and row.remaining is not None:
            used_pct = (row.capacity - row.remaining) / row.capacity * 100
        capacity_issue = (
            (row.capacity is not None and row.capacity <= 0)
            or (row.remaining is not None and row.remaining < 0)
            or (row.capacity is not None and row.remaining is not None
                and row.remaining > row.capacity)
        )
        loads.append({
            "load_id": row.load_id, "route": row.route, "vehicle": row.vehicle,
            "status": row.status, "capacity": row.capacity, "remaining": row.remaining,
            "uom": row.capacity_uom, "available_pct": row.available_pct,
            "used_pct": used_pct,
            "bar_pct": max(0, min(100, float(used_pct))) if used_pct is not None else 0,
            "capacity_issue": capacity_issue,
            "order_qty": row.order_qty, "order_value": row.order_value,
            "load_date": row.load_date, "ship_date": row.ship_date,
            "return_date": row.return_date, "load_time": row.load_time,
            "ship_time": row.ship_time, "return_time": row.return_time,
            "overdue": row.status != "SHIPPED" and row.ship_date is not None and row.ship_date < today,
            "due_today": row.status != "SHIPPED" and row.ship_date == today,
        })

    order_sync = populate_readiness(loads)
    history_sync = attach_manifest_fallback(loads)
    order_synced = order_sync["success"] is not None
    stages = []
    for key, label, description, icon, style in STAGE_META:
        members = [load for load in loads if load["status"] == key]
        stages.append({
            "key": key, "label": label, "description": description, "icon": icon,
            "style": style, "loads": members, **_totals(members, order_synced),
        })
    unknown = [load for load in loads if load["status"] not in LOAD_STAGES]
    if unknown:
        stages.append({
            "key": "OTHER", "label": "Other status", "description": "Review status in Epicor",
            "icon": "exclamation-triangle", "style": "other",
            "loads": unknown, **_totals(unknown, order_synced),
        })

    by_date: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for load in loads:
        if load["ship_date"]:
            stage_key = load["status"] if load["status"] in LOAD_STAGES else "OTHER"
            by_date[load["ship_date"]][stage_key] += 1
    days = sorted(by_date)
    timeline = {
        "labels": [day.strftime("%d %b %Y") for day in days],
        "datasets": [
            {"label": stage["label"], "data": [by_date[day][stage["key"]] for day in days],
             "style": stage["style"]}
            for stage in stages
        ],
    }
    latest = ImportBatch.query.filter_by(
        import_type=TransportLoadImporter.IMPORT_TYPE,
    ).order_by(ImportBatch.uploaded_at.desc(), ImportBatch.id.desc()).first()
    last_success = ImportBatch.query.filter_by(
        import_type=TransportLoadImporter.IMPORT_TYPE, status=ImportBatch.STATUS_SUCCESS,
    ).order_by(ImportBatch.uploaded_at.desc(), ImportBatch.id.desc()).first()
    routes = [
        name for (name,) in db.session.query(TransportLoad.route).distinct()
        .filter(TransportLoad.route.isnot(None), TransportLoad.route != "")
        .order_by(TransportLoad.route).all()
    ]
    return {
        "stages": stages, "summary": _totals(loads, order_synced), "timeline": timeline,
        "active_count": sum(load["status"] != "SHIPPED" for load in loads),
        "overdue_count": sum(load["overdue"] for load in loads),
        "today_count": sum(load["due_today"] for load in loads),
        "undated_count": sum(load["ship_date"] is None for load in loads),
        "capacity_issue_count": sum(load["capacity_issue"] for load in loads),
        "unknown_count": len(unknown), "routes": routes,
        "search": search, "route_f": route, "ship_from": ship_from, "ship_to": ship_to,
        "today": today, "latest_sync": latest, "last_success": last_success,
        "order_sync": order_sync,
        "manifest_sync": history_sync,
    }
