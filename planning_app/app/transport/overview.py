"""Management visuals derived from the same filtered snapshot as the load board."""

from collections import defaultdict
from decimal import Decimal

from app.sales.orders.models import ImportBatch


def build_management_summary(shipping: dict, bay: dict) -> dict:
    """Keep report definitions and snapshot ages visible without combining values."""
    horizon = shipping["shipping_horizon"]
    scale = max((abs(ready) + abs(held) for ready, held in zip(horizon["ready"], horizon["ready_hold"])), default=0)
    horizons = [
        {
            "label": label, "ready": ready, "held": held,
            "ready_width": abs(ready) / scale * 100 if scale else 0,
            "held_width": abs(held) / scale * 100 if scale else 0,
        }
        for label, ready, held in zip(horizon["labels"], horizon["ready"], horizon["ready_hold"])
    ]
    max_units = max((location["qty"] for location in bay["bay_board"]), default=0)
    bays = [{**location, "width": location["qty"] / max_units * 100 if max_units else 0}
            for location in bay["bay_board"]]
    snapshots = []
    for import_type, label in (("epicor_sales_open", "Open sales orders"), ("epicor_works_orders", "Works orders")):
        batches = ImportBatch.query.filter_by(import_type=import_type).order_by(
            ImportBatch.uploaded_at.desc(), ImportBatch.id.desc(),
        )
        snapshots.append({
            "label": label, "latest": batches.first(),
            "success": batches.filter_by(status=ImportBatch.STATUS_SUCCESS).first(),
        })
    return {
        "horizons": horizons, "bays": bays, "snapshots": snapshots,
        "negative_values": any(value < 0 for value in horizon["ready"] + horizon["ready_hold"]),
    }


def build_overview(board: dict) -> dict:
    """Use order values, not revenue; never average incompatible capacity units."""
    loads = [load for stage in board["stages"] for load in stage["loads"]]
    active = [load for load in loads if load["status"] != "SHIPPED"]
    days = defaultdict(list)
    for load in loads:
        if load["ship_date"]:
            days[load["ship_date"]].append(load)
    schedule = []
    for day, members in sorted(days.items()):
        segments = []
        for stage in board["stages"]:
            stage_loads = [load for load in members if load["status"] == stage["key"]]
            value = sum((load["order_value"] or Decimal(0) for load in stage_loads), Decimal(0))
            segments.append({"label": stage["label"], "style": stage["style"], "value": value})
        schedule.append({
            "date": day, "count": len(members), "segments": segments,
            "value": sum((segment["value"] for segment in segments), Decimal(0)),
            "missing_value": sum(load["order_value"] is None for load in members),
        })
    # Absolute scale also handles corrections represented as negative order values.
    scale = max((sum(abs(segment["value"]) for segment in day["segments"]) for day in schedule), default=Decimal(0))
    for day in schedule:
        for segment in day["segments"]:
            segment["width"] = float(abs(segment["value"]) / scale * 100) if scale else 0
    return {
        "schedule": schedule,
        "negative_values": any(load["order_value"] is not None and load["order_value"] < 0 for load in loads),
        "active_value": sum((load["order_value"] or Decimal(0) for load in active), Decimal(0)),
        "active_missing_value": sum(load["order_value"] is None for load in active),
        "undated_value": sum((load["order_value"] or Decimal(0) for load in loads if not load["ship_date"]), Decimal(0)),
        "held_loads": sum(bool(load["readiness"]["held"]) for load in active),
        "capacity_known": sum(load["used_pct"] is not None for load in active),
        "active_loads": sorted(active, key=lambda load: (
            load["ship_date"] is None, load["ship_date"] or board["today"], load["load_id"],
        )),
    }
