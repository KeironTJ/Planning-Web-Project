"""Retain shipped manifests from CSGTransManifestTransportAPI independently.

Run ``flask db upgrade`` then ``flask epicor sync transport_manifest``.
The registry also exposes this optional module to Admin and scheduled sync jobs.
Admin manual sync offers auto/custom dates; job schedules offer auto/fixed dates.
Fixed DateBefore is exclusive and fixed schedules repeat the same window.
Absent loads remain saved; returned shipped loads replace their saved contents.
Non-shipped observations preserve previous shipped contents and flag the changed
source status. This is not an immutable shipment audit. No quantities, values,
readiness or actual dispatch dates are inferred from jobs, cartons or packs.
Default sync selects load-header Date02 from 90 days ago through 90 days ahead
(inclusive), not individual detail rows. Override with DateFrom / DateBefore
ISO dates; DateBefore is exclusive. Older history stays saved. Corrections
outside the window require a wider range; blank-date loads require a full
reconciliation using the original CSGTransManifestTransport BAQ.
Fetch in seven-day slices to avoid Epicor's query timeout on wide date ranges.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.core.epicor_sync import EpicorBaqImporter
from app.extensions import db
from app.sales.orders.models import ImportBatch
from .models import TransportManifest, TransportManifestRelease
from .order_importer import _text, _values


def manifest_date_params(params: object) -> dict:
    """Validate manual/scheduled overrides; empty or auto uses rolling defaults."""
    if not isinstance(params, dict):
        raise ValueError("Manifest parameters must be an object.")
    if set(params) - {"mode", "DateFrom", "DateBefore"}:
        raise ValueError("Only DateFrom and DateBefore are supported for manifest sync.")
    mode = params.get("mode")
    if mode not in (None, "auto", "range"):
        raise ValueError("Manifest date mode must be auto or range.")
    if not params or params == {"mode": "auto"}:
        return {}
    if mode == "auto":
        raise ValueError("Auto mode must not contain fixed dates.")
    try:
        start = date.fromisoformat(params["DateFrom"])
        end = date.fromisoformat(params["DateBefore"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("DateFrom and DateBefore are both required in YYYY-MM-DD format.") from exc
    if start >= end:
        raise ValueError("DateBefore is exclusive and must be after DateFrom.")
    return {"DateFrom": start.isoformat(), "DateBefore": end.isoformat()}


def _positive_int(value: object, field: str) -> int:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number != number.to_integral_value():
            raise ValueError
        return int(number)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"CSGTransManifestTransport: invalid {field}: {value!r}") from exc


def _single(records: list[dict], field: str) -> str | None:
    values = _values(records, field)
    if len(values) > 1:
        raise ValueError(f"CSGTransManifestTransport: conflicting {field}")
    return values[0] if values else None


def _header_date(records: list[dict], field: str):
    dates = set()
    for record in records:
        value = record.get(field)
        if value is None or value == "":
            dates.add(None)
        else:
            try:
                dates.add(datetime.fromisoformat(str(value).replace("Z", "+00:00")).date())
            except ValueError as exc:
                raise ValueError(f"CSGTransManifestTransport: invalid {field}: {value!r}") from exc
    if len(dates) > 1:
        raise ValueError(f"CSGTransManifestTransport: conflicting {field}")
    return next(iter(dates))


class TransportManifestImporter(EpicorBaqImporter):
    BAQ_NAME = "CSGTransManifestTransportAPI"
    IMPORT_TYPE = "epicor_transport_manifest"
    PAGE_SIZE = 2000
    ALLOW_EMPTY_RESULT = True
    BAQ_PARAMS = {
        "$orderby": "UD110_Key1,OrderHed_OrderNum,OrderRel_OrderLine,OrderRel_OrderRelNum,"
                    "ShipDtl_PackNum,ShipDtl_PackLine,JobProd_JobNum",
    }

    def _target_table(self) -> str:
        return "transport_manifests"

    def get_dynamic_params(self) -> dict:
        today = date.today()
        return {
            "DateFrom": (today - timedelta(days=90)).isoformat(),
            "DateBefore": (today + timedelta(days=91)).isoformat(),
        }

    def _fetch_records(self, merged_params: dict) -> list[dict]:
        if set(merged_params) - {"$orderby", "DateFrom", "DateBefore"}:
            raise ValueError("CSGTransManifestTransport: filtered syncs are unsupported; complete load contents are required")
        try:
            start = date.fromisoformat(merged_params["DateFrom"])
            end = date.fromisoformat(merged_params["DateBefore"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("CSGTransManifestTransportAPI: DateFrom and DateBefore must be ISO dates") from exc
        if start >= end:
            raise ValueError("CSGTransManifestTransportAPI: DateFrom must precede exclusive DateBefore")
        records = []
        cursor = start
        while cursor < end:
            slice_end = min(cursor + timedelta(days=7), end)
            members = super()._fetch_records({
                **merged_params, "DateFrom": cursor.isoformat(),
                "DateBefore": slice_end.isoformat(),
            })
            for record in members:
                header_date = _header_date([record], "UD110_Date02")
                if header_date is None or not cursor <= header_date < slice_end:
                    raise ValueError("CSGTransManifestTransportAPI: returned header date outside requested window")
            records.extend(members)
            cursor = slice_end
        return records

    def _sync_records(self, records: list[dict], batch: ImportBatch, now: datetime) -> None:
        groups: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            load_id = _text(record.get("UD110_Key1"))
            if not load_id or len(load_id) > 100:
                raise ValueError("CSGTransManifestTransport: missing or invalid load ID")
            groups[load_id].append(record)

        existing = {
            row.load_id: row for row in TransportManifest.query.filter(
                TransportManifest.load_id.in_(list(groups)),
            ).all()
        }
        inserted = updated = skipped = 0
        for load_id, members in groups.items():
            statuses = {_text(row.get("UD110_Character03")).upper() for row in members}
            if len(statuses) != 1 or not next(iter(statuses)) or len(next(iter(statuses))) > 50:
                raise ValueError(f"CSGTransManifestTransport: invalid/conflicting status for {load_id}")
            status = next(iter(statuses))
            manifest = existing.get(load_id)
            if status != "SHIPPED":
                if manifest:
                    manifest.source_status = status
                    manifest.last_seen_at = now
                    updated += 1
                else:
                    skipped += 1
                continue

            route = _single(members, "UD110_Character02")
            _single(members, "UD110_Company")
            if route and len(route) > 255:
                raise ValueError(f"CSGTransManifestTransport: route too long for {load_id}")
            load_date = _header_date(members, "UD110_Date01")
            ship_date = _header_date(members, "UD110_Date02")
            releases: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
            for member in members:
                key = tuple(_positive_int(member.get(field), field) for field in (
                    "OrderHed_OrderNum", "OrderRel_OrderLine", "OrderRel_OrderRelNum",
                ))
                releases[key].append(member)
            contents = []
            for key, rows in sorted(releases.items()):
                for field in ("Customer_CustNum", "OrderRel_ShipToNum"):
                    _single(rows, field)
                packs = set()
                for row in rows:
                    pack, line = row.get("ShipDtl_PackNum"), row.get("ShipDtl_PackLine")
                    if pack not in (None, "", 0, "0"):
                        pack = _positive_int(pack, "ShipDtl_PackNum")
                        line = _positive_int(line, "ShipDtl_PackLine") if line not in (None, "", 0, "0") else None
                        packs.add((pack, line))
                    elif line not in (None, "", 0, "0"):
                        raise ValueError("CSGTransManifestTransport: pack line without pack number")
                contents.append(TransportManifestRelease(
                    order_num=key[0], order_line=key[1], rel_num=key[2],
                    customer=_single(rows, "Customer_Name"),
                    customer_po=_single(rows, "OrderHed_PONum"),
                    part_num=_single(rows, "OrderRel_PartNum"),
                    part_description=_single(rows, "Part_PartDescription"),
                    jobs=_values(rows, "JobProd_JobNum"),
                    packs=[{"number": pack, "line": line} for pack, line in
                           sorted(packs, key=lambda pair: (pair[0], pair[1] or 0))],
                ))
            if manifest is None:
                manifest = TransportManifest(load_id=load_id, first_observed_at=now)
                db.session.add(manifest)
                inserted += 1
            else:
                # Flush deletes before adding corrected identities under the unique key.
                manifest.releases.clear()
                db.session.flush()
                updated += 1
            manifest.route = route
            manifest.load_date = load_date
            manifest.ship_date = ship_date
            manifest.source_status = status
            manifest.last_seen_at = now
            manifest.contents_observed_at = now
            manifest.releases = contents
        batch.rows_inserted = inserted
        batch.rows_updated = updated
        batch.notes = (
            f"{inserted} new manifests; {updated} refreshed; {skipped} non-shipped loads excluded; "
            f"absent loads retained; header dates [{self._last_merged_params['DateFrom']}, "
            f"{self._last_merged_params['DateBefore']})"
        )
