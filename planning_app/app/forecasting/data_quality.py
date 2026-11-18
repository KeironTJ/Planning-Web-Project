"""Saved local VA05 observations and non-correcting quality checks."""

import hashlib
import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from itertools import groupby

from sqlalchemy import insert

from app.auth.models import AuditLog
from app.core.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.sales.orders.models import SalesOrder

from .models import (
    ForecastDataDecision,
    ForecastDataExtract,
    ForecastDataRecord,
)


RULES_VERSION = "va05-local-v1"
RULES = {
    "repeated_release": (
        "Release appears on multiple stored rows (review grain)"
    ),
    "missing_main": "Release has no main/no-assembly row",
    "conflicting_qty": (
        "Main rows have conflicting non-null release quantities"
    ),
    "conflicting_value": (
        "Main rows have conflicting non-null GBP release values"
    ),
    "missing_received": "Missing order-received date",
    "conflicting_received": "Order has conflicting non-null received dates",
    "missing_ship_by": "Missing required ship-by date",
    "ship_by_before_order": "Required ship-by date precedes order date",
    "missing_qty": "Missing main-row release quantity",
    "nonpositive_qty": "Nonpositive main-row release quantity",
    "missing_value": "Missing main-row GBP release value",
    "nonpositive_value": "Nonpositive main-row GBP release value",
    "missing_uom": "Missing inventory UOM",
}
DECISION_STATUSES = {
    "investigate": "Needs investigation",
    "accepted": "Accepted with evidence",
    "source_correction": "Source correction required",
}
SNAPSHOT_FIELDS = (
    "id",
    "order_num",
    "order_line",
    "rel_num",
    "assembly_seq",
    "job_num",
    "order_date",
    "order_received_date",
    "req_date",
    "release_qty",
    "release_price",
    "release_price_gbp",
    "ium",
    "currency_code",
    "exchange_rate",
    "imported_at",
)


def _serialize(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _release_findings(rows: list[dict]) -> list[str]:
    main_rows = [row for row in rows if row["assembly_seq"] in (None, 0)]
    findings = []
    if len(rows) > 1:
        findings.append("repeated_release")
    if not main_rows:
        findings.append("missing_main")
    for field, rule in (
        ("release_qty", "conflicting_qty"),
        ("release_price_gbp", "conflicting_value"),
    ):
        if (
            len({row[field] for row in main_rows if row[field] is not None})
            > 1
        ):
            findings.append(rule)
    return findings


def _row_findings(row: dict) -> list[str]:
    findings = []
    if row["order_received_date"] is None:
        findings.append("missing_received")
    if row["req_date"] is None:
        findings.append("missing_ship_by")
    elif row["order_date"] and row["req_date"] < row["order_date"]:
        findings.append("ship_by_before_order")
    if not (row["ium"] or "").strip():
        findings.append("missing_uom")
    if row["assembly_seq"] in (None, 0):
        for field, missing, nonpositive in (
            ("release_qty", "missing_qty", "nonpositive_qty"),
            ("release_price_gbp", "missing_value", "nonpositive_value"),
        ):
            if row[field] is None:
                findings.append(missing)
            elif row[field] <= 0:
                findings.append(nonpositive)
    return findings


def save_va05_extract(user_id: int) -> ForecastDataExtract:
    """Capture parsed local values atomically, without running an import."""
    extract = ForecastDataExtract(
        captured_by_id=user_id,
        source="bskyVA05v1",
        rules_version=RULES_VERSION,
        row_count=0,
        content_hash="",
        source_settings={
            "origin": "local_sales_orders",
            "selection": "All locally retained rows at capture",
            "baq_parameters": "Not retained by the operational importer",
            "original_intake": "Unverified: observed release values only",
            "fields": list(SNAPSHOT_FIELDS),
        },
        summary={},
    )
    db.session.add(extract)
    db.session.flush()
    query = (
        db.session.query(
            *(getattr(SalesOrder, field) for field in SNAPSHOT_FIELDS)
        )
        .order_by(
            SalesOrder.order_num,
            SalesOrder.order_line,
            SalesOrder.rel_num,
            SalesOrder.id,
        )
        .yield_per(500)
    )
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {
                "source": extract.source,
                "rules_version": RULES_VERSION,
                "source_settings": extract.source_settings,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    counts = Counter()
    row_count = flagged_count = release_count = order_count = 0
    pending = []
    for _, order_rows in groupby(query, key=lambda row: row.order_num):
        order_count += 1
        # Check order-level dates across every release, not just each row.
        order_data = [dict(row._mapping) for row in order_rows]
        received_dates = {
            row["order_received_date"]
            for row in order_data
            if row["order_received_date"] is not None
        }
        for _, release_rows in groupby(
            order_data,
            key=lambda row: (row["order_line"], row["rel_num"]),
        ):
            release_count += 1
            rows = list(release_rows)
            common = _release_findings(rows)
            if len(received_dates) > 1:
                common.append("conflicting_received")
            for row in rows:
                findings = common + _row_findings(row)
                data = {
                    field: _serialize(row[field]) for field in SNAPSHOT_FIELDS
                }
                digest.update(b"\n")
                digest.update(
                    json.dumps(
                        data,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                pending.append(
                    {
                        "extract_id": extract.id,
                        "source_row_id": row["id"],
                        "data": data,
                        "findings": findings,
                        "has_findings": bool(findings),
                    }
                )
                row_count += 1
                flagged_count += bool(findings)
                counts.update(findings)
                if len(pending) >= 500:
                    db.session.execute(insert(ForecastDataRecord), pending)
                    pending.clear()
    if not row_count:
        db.session.rollback()
        raise ValidationError(
            "No local VA05 rows to capture. Import sales orders first."
        )
    if pending:
        db.session.execute(insert(ForecastDataRecord), pending)
    extract.row_count = row_count
    extract.content_hash = digest.hexdigest()
    extract.summary = {
        "flagged_rows": flagged_count,
        "release_count": release_count,
        "order_count": order_count,
        "rules": RULES,
        "counts": dict(counts),
    }
    db.session.add(
        AuditLog(
            user_id=user_id,
            action="forecast_data_extract_saved",
            resource="forecast_data_extract",
            details=json.dumps(
                {
                    "extract_id": extract.id,
                    "row_count": row_count,
                    "content_hash": extract.content_hash,
                    "rules_version": RULES_VERSION,
                }
            ),
        )
    )
    db.session.commit()
    return extract


def save_data_decision(
    record_id: int,
    status: str,
    rationale: str,
    user_id: int,
) -> ForecastDataDecision:
    """Append evidence without changing the saved values or findings."""
    if status not in DECISION_STATUSES:
        raise ValidationError("Choose an available review outcome.")
    rationale = rationale.strip()
    if not rationale or len(rationale) > 2000:
        raise ValidationError(
            "Provide review evidence between 1 and 2,000 characters."
        )
    record = db.session.get(ForecastDataRecord, record_id)
    if record is None:
        raise NotFoundError("The saved data record does not exist.")
    if not record.has_findings:
        raise ValidationError("This saved record has no findings to review.")
    decision = ForecastDataDecision(
        record_id=record.id,
        status=status,
        rationale=rationale,
        reviewed_by_id=user_id,
    )
    db.session.add(decision)
    db.session.add(
        AuditLog(
            user_id=user_id,
            action="forecast_data_decision_saved",
            resource="forecast_data_record",
            details=json.dumps(
                {
                    "extract_id": record.extract_id,
                    "record_id": record.id,
                    "status": status,
                    "rationale": rationale,
                }
            ),
        )
    )
    db.session.commit()
    return decision
