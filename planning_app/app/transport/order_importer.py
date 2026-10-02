"""Assigned release snapshots for CSGTransportWBOrders.

Run ``flask db upgrade`` then ``flask epicor sync transport_orders``.
For scheduled jobs, add transport_loads followed by transport_orders so the
header and readiness snapshots refresh together. Configure intervals in Admin.

Readiness is based on all returned Calculated_JobStatus values, not the BAQ's
Calculated_OrderStatus, which differs even on packed/production-complete loads.
Multiple joined rows collapse to one order/line/release. Conflicting assignments,
quantities or release identities abort the import and retain the prior snapshot.
Unassigned orders are deliberately excluded; this is not a production backlog.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.core.epicor_sync import EpicorBaqImporter
from app.extensions import db
from app.sales.orders.models import ImportBatch
from .models import TransportOrderRelease


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _identity(record: dict, field: str) -> int:
    try:
        value = Decimal(str(record.get(field)))
        if not value.is_finite() or value <= 0 or value != value.to_integral_value():
            raise ValueError
        return int(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"CSGTransportWBOrders: invalid {field}: {record.get(field)!r}") from exc


def _quantity(record: dict) -> Decimal | None:
    value = record.get("OrderRel_SellingReqQty")
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"CSGTransportWBOrders: invalid release quantity: {value!r}") from exc


def _hold(value: object) -> bool | None:
    if value is None or value == "":
        return None
    text = _text(value).lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    raise ValueError(f"CSGTransportWBOrders: invalid hold flag: {value!r}")


def _combined_hold(records: list[dict], field: str) -> bool | None:
    values = [_hold(record.get(field)) for record in records]
    if True in values:
        return True
    return None if None in values else False


def _values(records: list[dict], field: str) -> list[str]:
    return sorted({_text(record.get(field)) for record in records if _text(record.get(field))})


class TransportOrderImporter(EpicorBaqImporter):
    BAQ_NAME = "CSGTransportWBOrders"
    IMPORT_TYPE = "epicor_transport_orders"
    PAGE_SIZE = 2000

    def _target_table(self) -> str:
        return "transport_order_releases"

    def _sync_records(self, records: list[dict], batch: ImportBatch, now: datetime) -> None:
        groups: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
        unassigned = 0
        for record in records:
            if not _text(record.get("OrderRel_CSGTransportNum_c")):
                unassigned += 1
                continue
            key = (
                _identity(record, "OrderHed_OrderNum"),
                _identity(record, "OrderRel_OrderLine"),
                _identity(record, "OrderRel_OrderRelNum"),
            )
            groups[key].append(record)

        releases = []
        for key, members in groups.items():
            assignments = _values(members, "OrderRel_CSGTransportNum_c")
            quantities = {_quantity(record) for record in members}
            if len(assignments) != 1 or len(quantities) != 1:
                raise ValueError(f"CSGTransportWBOrders: conflicting assignment or quantity for {key}")
            for field in ("Customer_Name", "OrderRel_PartNum", "Part_PartDescription"):
                if len(_values(members, field)) > 1:
                    raise ValueError(f"CSGTransportWBOrders: conflicting {field} for {key}")
            statuses = [_text(record.get("Calculated_JobStatus")).lower() for record in members]
            if all(status == "complete" for status in statuses):
                state = "complete"
            elif any(status in ("in progress", "unreleased", "not complete") for status in statuses):
                state = "outstanding"
            else:
                state = "unknown"
            # Unknown/no-job rows prevent an all-complete conclusion.
            releases.append(TransportOrderRelease(
                load_id=assignments[0], order_num=key[0], order_line=key[1], rel_num=key[2],
                customer=next(iter(_values(members, "Customer_Name")), None),
                part_num=next(iter(_values(members, "OrderRel_PartNum")), None),
                part_description=next(iter(_values(members, "Part_PartDescription")), None),
                quantity=next(iter(quantities)),
                production_state=state,
                order_statuses=_values(members, "Calculated_OrderStatus"),
                job_statuses=_values(members, "Calculated_JobStatus"),
                jobs=_values(members, "JobProd_JobNum"),
                locations=_values(members, "PartWip_BinNum"),
                pack_refs=_values(members, "ShipDtl_PackNum"),
                order_held=_combined_hold(members, "OrderHed_OrderHeld"),
                customer_credit_hold=_combined_hold(members, "Customer_CreditHold"),
                so_credit_hold=_combined_hold(members, "Calculated_SOCreditHold"),
                imported_at=now,
            ))

        TransportOrderRelease.query.delete()
        db.session.add_all(releases)
        batch.rows_inserted = len(releases)
        batch.notes = (
            f"{len(releases)} assigned releases; {unassigned} unassigned rows excluded; "
            f"{len(records) - unassigned - len(releases)} joined rows collapsed"
        )
