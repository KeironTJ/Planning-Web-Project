"""Read-only retained manifests; never feed history into production readiness."""

from datetime import date

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.sales.orders.models import ImportBatch
from .manifest_importer import TransportManifestImporter
from .history_insights import build_history_insights
from .models import TransportManifest, TransportManifestRelease, TransportOrderRelease


def manifest_sync() -> dict:
    batches = ImportBatch.query.filter_by(
        import_type=TransportManifestImporter.IMPORT_TYPE,
    ).order_by(ImportBatch.uploaded_at.desc(), ImportBatch.id.desc())
    return {
        "latest": batches.first(),
        "success": batches.filter_by(status=ImportBatch.STATUS_SUCCESS).first(),
    }


def get_manifest_history(
    search: str = "", route: str = "", ship_from: date | None = None,
    ship_to: date | None = None, page: int = 1,
) -> dict:
    search, route = search.strip(), route.strip()
    query = TransportManifest.query
    if search:
        term = f"%{search}%"
        release_match = TransportManifest.id.in_(
            db.session.query(TransportManifestRelease.manifest_id).filter(db.or_(
                db.cast(TransportManifestRelease.order_num, db.String).ilike(term),
                TransportManifestRelease.customer.ilike(term),
                TransportManifestRelease.customer_po.ilike(term),
                TransportManifestRelease.part_num.ilike(term),
            )),
        )
        query = query.filter(db.or_(TransportManifest.load_id.ilike(term), release_match))
    if route:
        query = query.filter(TransportManifest.route == route)
    if ship_from:
        query = query.filter(TransportManifest.ship_date >= ship_from)
    if ship_to:
        query = query.filter(TransportManifest.ship_date <= ship_to)
    pagination = query.options(selectinload(TransportManifest.releases)).order_by(
        TransportManifest.ship_date.desc().nullslast(), TransportManifest.load_id,
    ).paginate(page=page, per_page=20, error_out=False)
    routes = [
        name for (name,) in db.session.query(TransportManifest.route).distinct()
        .filter(TransportManifest.route.isnot(None), TransportManifest.route != "")
        .order_by(TransportManifest.route).all()
    ]
    return {
        "pagination": pagination, "manifests": pagination.items, "routes": routes,
        "search": search, "route_f": route, "ship_from": ship_from, "ship_to": ship_to,
        "manifest_sync": manifest_sync(),
        "insights": build_history_insights(query),
    }


def attach_manifest_fallback(loads: list[dict]) -> dict:
    candidates = [load["load_id"] for load in loads
                  if load["status"] == "SHIPPED" and not load["readiness"]["count"]]
    current_ids = set()
    manifests = {}
    if candidates:
        # Check actual current rows even if their successful sync audit was pruned.
        current_ids = {
            load_id for (load_id,) in db.session.query(TransportOrderRelease.load_id)
            .filter(TransportOrderRelease.load_id.in_(candidates)).distinct().all()
        }
        manifests = {
            row.load_id: row for row in TransportManifest.query.options(
                selectinload(TransportManifest.releases),
            ).filter(
                TransportManifest.load_id.in_(candidates),
                TransportManifest.source_status == "SHIPPED",
            ).all()
        }
    for load in loads:
        load["manifest"] = manifests.get(load["load_id"]) if load["load_id"] not in current_ids else None
    return manifest_sync()
