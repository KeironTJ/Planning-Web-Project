"""Aggregate complete filtered manifests, not a page or joined detail rows.

Orders per load counts distinct order appearances on each saved manifest.
Monthly counts use scheduled header dates, include future dates, and show the
latest twelve calendar months in scope. Neither chart measures dispatch volume,
shipped units, value, or physical utilisation.
"""

from datetime import date

from app.extensions import db
from .models import TransportManifest, TransportManifestRelease


def _bars(rows: list[dict]) -> list[dict]:
    maximum = max((row["count"] for row in rows), default=0)
    for row in rows:
        row["width"] = round(row["count"] / maximum * 100, 2) if maximum else 0
    return rows


def build_history_insights(filtered_query, today: date | None = None) -> dict:
    today = today or date.today()
    selected = filtered_query.with_entities(
        TransportManifest.id, TransportManifest.route,
        TransportManifest.ship_date, TransportManifest.source_status,
    ).subquery()
    totals = db.session.query(
        db.func.count(selected.c.id),
        db.func.min(selected.c.ship_date), db.func.max(selected.c.ship_date),
        db.func.sum(db.case((selected.c.ship_date.is_(None), 1), else_=0)),
        db.func.sum(db.case((selected.c.ship_date > today, 1), else_=0)),
        db.func.sum(db.case((selected.c.source_status != "SHIPPED", 1), else_=0)),
    ).one()
    count = totals[0]
    releases = db.session.query(TransportManifestRelease).join(
        selected, selected.c.id == TransportManifestRelease.manifest_id,
    )
    release_count, order_count = releases.with_entities(
        db.func.count(TransportManifestRelease.id),
        db.func.count(db.distinct(TransportManifestRelease.order_num)),
    ).one()
    per_load = releases.with_entities(
        TransportManifestRelease.manifest_id,
        db.func.count(db.distinct(TransportManifestRelease.order_num)).label("orders"),
    ).group_by(TransportManifestRelease.manifest_id).subquery()
    order_appearances = db.session.query(db.func.sum(per_load.c.orders)).scalar() or 0
    per_order = releases.with_entities(
        TransportManifestRelease.order_num,
        db.func.count(db.distinct(TransportManifestRelease.manifest_id)).label("loads"),
    ).group_by(TransportManifestRelease.order_num).subquery()
    multi_load_orders = db.session.query(db.func.count()).select_from(per_order).filter(
        per_order.c.loads > 1,
    ).scalar()

    route_name = db.func.nullif(selected.c.route, "")
    route_counts = db.session.query(
        route_name, db.func.count(selected.c.id).label("count"),
    ).group_by(route_name).order_by(
        db.func.count(selected.c.id).desc(), route_name,
    ).all()
    routes = [
        {"label": route or "No route", "route": route, "count": amount,
         "share": round(amount / count * 100, 1)}
        for route, amount in route_counts[:5]
    ]
    remainder = sum(amount for _, amount in route_counts[5:])
    if remainder:
        routes.append({
            "label": "Other routes", "route": None, "count": remainder,
            "share": round(remainder / count * 100, 1),
        })

    monthly = []
    if totals[1] and totals[2]:
        last_index = totals[2].year * 12 + totals[2].month - 1
        first_index = max(totals[1].year * 12 + totals[1].month - 1, last_index - 11)
        first_month = date(first_index // 12, first_index % 12 + 1, 1)
        year = db.extract("year", selected.c.ship_date)
        month = db.extract("month", selected.c.ship_date)
        amounts = {
            (int(y), int(m)): amount
            for y, m, amount in db.session.query(
                year, month, db.func.count(selected.c.id),
            ).filter(selected.c.ship_date >= first_month).group_by(year, month).all()
        }
        for index in range(first_index, last_index + 1):
            day = date(index // 12, index % 12 + 1, 1)
            monthly.append({
                "label": day.strftime("%b %Y"),
                "count": amounts.get((day.year, day.month), 0),
            })

    buckets = [(0, 0, "No orders"), (1, 1, "1 order"), (2, 5, "2-5 orders"),
               (6, 10, "6-10 orders"), (11, 20, "11-20 orders"), (21, None, "21+ orders")]
    distribution = []
    order_counts = db.func.coalesce(per_load.c.orders, 0)
    for lower, upper, _ in buckets:
        condition = order_counts >= lower
        if upper is not None:
            condition = db.and_(condition, order_counts <= upper)
        distribution.append(db.func.sum(db.case((condition, 1), else_=0)))
    bucket_counts = db.session.query(*distribution).select_from(selected).outerjoin(
        per_load, per_load.c.manifest_id == selected.c.id,
    ).one()
    return {
        "count": count, "orders": order_count, "releases": release_count,
        "orders_per_load": round(order_appearances / count, 1) if count else None,
        "multi_load_orders": multi_load_orders,
        "undated": totals[3] or 0, "future": totals[4] or 0,
        "status_changed": totals[5] or 0,
        "monthly": _bars(monthly), "routes": _bars(routes),
        "distribution": _bars([
            {"label": bucket[2], "count": amount or 0}
            for bucket, amount in zip(buckets, bucket_counts)
        ]),
    }
