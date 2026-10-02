"""Conservative production-readiness summaries for the displayed loads."""

from collections import defaultdict
from decimal import Decimal

from app.sales.orders.models import ImportBatch
from .models import TransportOrderRelease
from .order_importer import TransportOrderImporter


def summarise_readiness(loads: list[dict], synced: bool) -> dict:
    """Aggregate observed releases, not an average of load percentages.

    Coverage warnings stay per load: opposite quantity mismatches must not
    cancel each other out and imply that the group has complete coverage.
    """
    unique = {load["load_id"]: load["readiness"] for load in loads}
    totals = {
        key: sum(ready[key] for ready in unique.values())
        for key in ("count", "complete", "outstanding", "unknown", "held", "unchecked_holds")
    }
    coverage_issues = sum(not ready["coverage_matches"] for ready in unique.values()) if synced else 0
    return {
        **totals, "synced": synced, "coverage_issues": coverage_issues,
        "pct": round(totals["complete"] / totals["count"] * 100, 1) if totals["count"] else 0,
        "clear": (
            synced and totals["count"] > 0 and totals["complete"] == totals["count"]
            and not totals["held"] and not totals["unchecked_holds"] and not coverage_issues
        ),
    }


def _group_orders(releases: list[dict]) -> list[dict]:
    """Aggregate only the releases assigned to this load, retaining their detail."""
    grouped: dict[int, list[dict]] = defaultdict(list)
    for release in releases:
        grouped[release["order_num"]].append(release)
    orders = []
    for order_num, members in sorted(grouped.items()):
        states = {release["state"] for release in members}
        orders.append({
            "order_num": order_num, "releases": members, "count": len(members),
            "customers": list(dict.fromkeys(release["customer"] for release in members)),
            "products": list(dict.fromkeys(
                (release["part_num"], release["part_description"]) for release in members
            )),
            "quantity": (
                sum((release["quantity"] for release in members), Decimal(0))
                if all(release["quantity"] is not None for release in members) else None
            ),
            "complete": sum(release["state"] == "complete" for release in members),
            "state": "unknown" if "unknown" in states else (
                "outstanding" if "outstanding" in states else "complete"
            ),
            "holds": sorted({hold for release in members for hold in release["holds"]}),
            "hold_unknown": any(release["hold_unknown"] for release in members),
        })
    return orders


def populate_readiness(loads: list[dict]) -> dict:
    """Expose assigned releases grouped by order and a subset needing attention.

    Counts keep production completion and holds separate. Header quantity
    reconciliation checks data coverage, not packing or physical loading.
    """
    batches = ImportBatch.query.filter_by(
        import_type=TransportOrderImporter.IMPORT_TYPE,
    ).order_by(ImportBatch.uploaded_at.desc(), ImportBatch.id.desc())
    latest = batches.first()
    success = batches.filter_by(status=ImportBatch.STATUS_SUCCESS).first()
    grouped: dict[str, list[TransportOrderRelease]] = defaultdict(list)
    if success and loads:
        for release in TransportOrderRelease.query.filter(
            TransportOrderRelease.load_id.in_([load["load_id"] for load in loads]),
        ).order_by(
            TransportOrderRelease.order_num, TransportOrderRelease.order_line,
            TransportOrderRelease.rel_num,
        ).all():
            grouped[release.load_id].append(release)

    for load in loads:
        releases = grouped[load["load_id"]]
        complete = sum(release.production_state == "complete" for release in releases)
        outstanding = sum(release.production_state == "outstanding" for release in releases)
        unknown = sum(release.production_state == "unknown" for release in releases)
        issues = []
        contents = []
        held = 0
        unchecked_holds = 0
        packs: set[str] = set()
        for release in releases:
            reasons = []
            if release.order_held:
                reasons.append("Order hold")
            if release.so_credit_hold:
                reasons.append("SO credit hold")
            if release.customer_credit_hold:
                reasons.append("Customer credit hold")
            hold_unknown = any(value is None for value in (
                release.order_held, release.so_credit_hold, release.customer_credit_hold,
            ))
            held += bool(reasons)
            unchecked_holds += hold_unknown
            packs.update(release.pack_refs)
            detail = {
                "order_num": release.order_num, "order_line": release.order_line,
                "rel_num": release.rel_num, "customer": release.customer,
                "part_num": release.part_num, "part_description": release.part_description,
                "quantity": release.quantity, "state": release.production_state,
                "job_statuses": release.job_statuses, "order_statuses": release.order_statuses,
                "jobs": release.jobs, "locations": release.locations,
                "pack_refs": release.pack_refs, "holds": reasons, "hold_unknown": hold_unknown,
            }
            contents.append(detail)
            if release.production_state != "complete" or reasons or hold_unknown:
                issues.append(detail)
        quantity = sum((release.quantity or Decimal(0) for release in releases), Decimal(0))
        quantity_known = all(release.quantity is not None for release in releases)
        coverage_matches = (
            success is not None and load["order_qty"] is not None and quantity_known
            and abs(quantity - load["order_qty"]) < Decimal("0.0001")
        )
        load["readiness"] = {
            "synced": success is not None, "count": len(releases), "complete": complete,
            "outstanding": outstanding, "unknown": unknown, "held": held,
            "unchecked_holds": unchecked_holds, "issues": issues, "releases": contents,
            "orders": _group_orders(contents),
            "quantity": quantity if quantity_known else None,
            "coverage_matches": coverage_matches, "pack_refs": sorted(packs),
            "pct": round(complete / len(releases) * 100, 1) if releases else 0,
        }
    return {"latest": latest, "success": success}
