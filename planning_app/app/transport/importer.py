"""Audited, full-snapshot CSGTransportWB import.

Run ``flask epicor sync transport_loads`` after ``flask db upgrade``.
The registry also exposes this importer in Admin > Epicor Data Sync and
scheduled sync jobs. Page refreshes read the last successful snapshot;
they do not call Epicor. Invalid data aborts the sync, retaining that snapshot.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.core.epicor_sync import EpicorBaqImporter
from app.extensions import db
from app.sales.orders.models import ImportBatch
from .models import TransportLoad


def _number(record: dict, field: str) -> Decimal | None:
    value = record.get(field)
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"CSGTransportWB: invalid number in {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"CSGTransportWB: non-finite number in {field}")
    return result


def _date(record: dict, field: str) -> date | None:
    value = record.get(field)
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ValueError(f"CSGTransportWB: invalid date in {field}: {value!r}") from exc


class TransportLoadImporter(EpicorBaqImporter):
    BAQ_NAME = "CSGTransportWB"
    IMPORT_TYPE = "epicor_transport_loads"

    def _target_table(self) -> str:
        return "transport_loads"

    def _sync_records(
        self, records: list[dict], batch: ImportBatch, now: datetime
    ) -> None:
        rows = []
        seen: dict[str, dict] = {}
        for record in records:
            load_id = str(record.get("UD110_Key1") or "").strip()
            status = str(record.get("UD110_Character03") or "").strip().upper()
            source_id = str(record.get("SysRowID") or record.get("RowIdent") or load_id).strip()
            if not load_id or not status:
                raise ValueError("CSGTransportWB: every load must have a transport ID and status")
            if source_id in seen:
                if seen[source_id] != record:
                    raise ValueError(f"CSGTransportWB: conflicting rows for {source_id}")
                continue
            seen[source_id] = record
            rows.append(TransportLoad(
                source_row_id=source_id,
                load_id=load_id,
                transport_code=record.get("UD110_Character01"),
                route=record.get("UD110_Character02"),
                vehicle=record.get("UD110_Character08"),
                status=status,
                capacity=_number(record, "UD110_Number05"),
                remaining=_number(record, "UD110_Number06"),
                capacity_uom=record.get("UD110_ShortChar04"),
                available_pct=_number(record, "Calculated_Percentage"),
                order_qty=_number(record, "Calculated_OrderQty"),
                order_value=_number(record, "Calculated_OrderVal"),
                load_date=_date(record, "UD110_Date01"),
                ship_date=_date(record, "UD110_Date02"),
                return_date=_date(record, "UD110_Date03"),
                load_time=record.get("Calculated_Calculated_LoadTimeCalc"),
                ship_time=record.get("Calculated_Calculated_ShipTimeCalc"),
                return_time=record.get("Calculated_Calculated_ReturnTimeCalc"),
                imported_at=now,
            ))

        TransportLoad.query.delete()
        db.session.add_all(rows)
        batch.rows_inserted = len(rows)
        if len(rows) != len(records):
            batch.notes = f"Skipped {len(records) - len(rows)} identical BAQ duplicates"
