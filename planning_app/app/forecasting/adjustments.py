"""Evidence-backed adjustments used to create immutable derived extracts."""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import groupby

from sqlalchemy import func, insert
from sqlalchemy.orm import aliased, lazyload

from app.auth.models import AuditLog
from app.core.exceptions import ValidationError
from app.extensions import db

from .data_quality import SNAPSHOT_FIELDS, _append_data_decision, _lock_extract, _release_findings, _row_findings, _serialize
from .models import ForecastDataDecision, ForecastDataExtract, ForecastDataRecord, ForecastReleaseAdjustment, ForecastRowExclusion
from .weekly_intake import release_order_date


KEY_FIELDS = ("order_num", "order_line", "rel_num")
DATE_FIELDS = ("order_date", "order_received_date", "req_date")
NUMBER_FIELDS = ("release_qty", "release_price", "release_price_gbp", "exchange_rate")
ADJUSTABLE = ("release_qty", "release_price_gbp", "void_line", "order_date", "req_date")


def _key(data):
    return tuple(data.get(field) for field in KEY_FIELDS)


def _order_rows(extract_id, order_num):
    return ForecastDataRecord.query.filter(
        ForecastDataRecord.extract_id == extract_id,
        ForecastDataRecord.data["order_num"].as_integer() == order_num,
    ).order_by(ForecastDataRecord.id).all()


def order_signature(rows):
    observations = sorted(_row_signature(data, findings) for data, findings in rows)
    return hashlib.sha256(json.dumps(observations, separators=(",", ":")).encode()).hexdigest()


def _row_signature(data, findings):
    return json.dumps({
        "data": {key: value for key, value in data.items() if key not in ("id", "imported_at")},
        "findings": sorted(findings),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def latest_adjustments(extract_id):
    latest = {}
    for entry in ForecastReleaseAdjustment.query.filter_by(extract_id=extract_id).order_by(
        ForecastReleaseAdjustment.id
    ):
        latest[(entry.order_num, entry.order_line, entry.rel_num)] = entry
    return latest


def latest_row_exclusions(extract_id):
    latest = {}
    for entry in ForecastRowExclusion.query.filter_by(extract_id=extract_id).order_by(ForecastRowExclusion.id):
        latest[entry.record_id] = entry
    return latest


def row_exclusion_suggestions(extract, records, current):
    """Suggest only uniquely identifiable, unchanged saved observations."""
    targets = [
        record for record in records
        if record.id not in current and set(SNAPSHOT_FIELDS).issubset(record.data)
        and all(record.data.get(field) is not None for field in KEY_FIELDS)
    ]
    if extract.derived_from_id is not None or not targets:
        return {}
    orders = {record.data["order_num"] for record in targets}
    prior_entries = (
        db.session.query(ForecastRowExclusion, ForecastDataExtract)
        .join(ForecastDataRecord, ForecastRowExclusion.record_id == ForecastDataRecord.id)
        .join(ForecastDataExtract, ForecastRowExclusion.extract_id == ForecastDataExtract.id)
        .filter(
            ForecastDataExtract.id < extract.id, ForecastDataExtract.derived_from_id.is_(None),
            ForecastDataExtract.source == extract.source, ForecastDataExtract.rules_version == extract.rules_version,
            ForecastDataRecord.data["order_num"].as_integer().in_(orders),
        ).order_by(ForecastDataExtract.id.desc(), ForecastRowExclusion.id.desc()).all()
    )
    prior_entries = [(entry, prior) for entry, prior in prior_entries
                     if prior.source_settings.get("capture_environment") == extract.source_settings.get("capture_environment")]
    if not prior_entries:
        return {}
    extract_ids = {prior.id for _, prior in prior_entries} | {extract.id}
    rows = ForecastDataRecord.query.filter(
        ForecastDataRecord.extract_id.in_(extract_ids),
        ForecastDataRecord.data["order_num"].as_integer().in_(orders),
    ).with_entities(ForecastDataRecord.extract_id, ForecastDataRecord.data, ForecastDataRecord.findings)
    counts = Counter(
        (extract_id, data["order_num"], _row_signature(data, findings))
        for extract_id, data, findings in rows
    )
    targets_by_signature = {
        (record.data["order_num"], _row_signature(record.data, record.findings)): record
        for record in targets
        if counts[(extract.id, record.data["order_num"], _row_signature(record.data, record.findings))] == 1
    }
    suggestions = {}
    seen_records = set()
    resolved = set()
    for entry, prior in prior_entries:
        if entry.record_id in seen_records:
            continue
        seen_records.add(entry.record_id)
        record = entry.record
        key = (record.data["order_num"], _row_signature(record.data, record.findings))
        target = targets_by_signature.get(key)
        if target is None or key in resolved:
            continue
        resolved.add(key)
        if counts[(prior.id, *key)] == 1 and entry.action == "exclude":
            suggestions[target.id] = (entry, prior)
    return suggestions


def row_exclusion_suggestion_ids(extract, current):
    if extract.derived_from_id is not None:
        return []
    orders = [order for (order,) in (
        db.session.query(ForecastDataRecord.data["order_num"].as_integer())
        .join(ForecastRowExclusion, ForecastRowExclusion.record_id == ForecastDataRecord.id)
        .join(ForecastDataExtract, ForecastRowExclusion.extract_id == ForecastDataExtract.id)
        .filter(
            ForecastDataExtract.id < extract.id, ForecastDataExtract.derived_from_id.is_(None),
            ForecastDataExtract.source == extract.source, ForecastDataExtract.rules_version == extract.rules_version,
        ).distinct()
    ) if order is not None]
    ids = []
    for offset in range(0, len(orders), 200):
        records = ForecastDataRecord.query.options(lazyload(ForecastDataRecord.decisions)).filter(
            ForecastDataRecord.extract_id == extract.id,
            ForecastDataRecord.data["order_num"].as_integer().in_(orders[offset:offset + 200]),
        ).all()
        ids.extend(row_exclusion_suggestions(extract, records, current))
    return ids


def confirm_row_exclusion_suggestions(extract_id, selections, user_id, identity):
    record_ids = {record_id for record_id, _ in selections}
    if not 1 <= len(selections) <= 100 or len(record_ids) != len(selections):
        raise ValidationError("Select between 1 and 100 distinct suggested exclusions.")
    extract = db.session.get(ForecastDataExtract, extract_id)
    if extract is None:
        raise ValidationError("The saved extract no longer exists.")
    records = ForecastDataRecord.query.filter(
        ForecastDataRecord.extract_id == extract_id, ForecastDataRecord.id.in_(record_ids),
    ).all()
    orders = {record.data.get("order_num") for record in records if record.data.get("order_num") is not None}
    prior_ids = db.session.query(ForecastDataExtract.id).join(ForecastDataRecord).filter(
        ForecastDataExtract.id < extract_id, ForecastDataExtract.derived_from_id.is_(None),
        ForecastDataExtract.source == extract.source, ForecastDataExtract.rules_version == extract.rules_version,
        ForecastDataRecord.data["order_num"].as_integer().in_(orders),
    ).distinct().order_by(ForecastDataExtract.id).all()
    for (prior_id,) in prior_ids:
        _lock_extract(prior_id, None)
    extract = _lock_extract(extract_id, identity)
    db.session.expire_all()
    current = latest_row_exclusions(extract_id)
    suggestions = row_exclusion_suggestions(extract, records, current)
    if len(records) != len(record_ids) or any(
        record_id not in suggestions or suggestions[record_id][0].id != prior_id
        for record_id, prior_id in selections
    ):
        raise ValidationError("An exclusion suggestion has changed. Check the current suggestions before confirming.")
    for record_id, prior_id in selections:
        prior = suggestions[record_id][0]
        entry = ForecastRowExclusion(
            extract_id=extract_id, record_id=record_id, action="exclude",
            rationale=prior.rationale, reused_from_id=prior_id, created_by_id=user_id,
        )
        db.session.add(entry)
        db.session.flush()
        db.session.add(AuditLog(
            user_id=user_id, action="forecast_row_exclusion_saved", resource="forecast_data_extract",
            details=json.dumps({"extract_id": extract_id, "row_exclusion_id": entry.id,
                                "record_id": record_id, "action": "exclude", "reused_from_id": prior_id}),
        ))
    db.session.commit()
    return len(selections)


def save_row_exclusions(extract_id, record_ids, action, rationale, user_id, identity, expected_revision):
    source = _lock_extract(extract_id, identity)
    if source.derived_from_id is not None:
        raise ValidationError("Make exclusions on the original extract, not an adjusted extract.")
    if action not in ("exclude", "clear"):
        raise ValidationError("Choose whether to exclude or restore selected rows.")
    rationale = rationale.strip()
    if not rationale or len(rationale) > 2000:
        raise ValidationError("Provide evidence between 1 and 2,000 characters.")
    if not 1 <= len(record_ids) <= 100 or len(set(record_ids)) != len(record_ids):
        raise ValidationError("Select between 1 and 100 distinct saved rows.")
    latest = latest_row_exclusions(extract_id)
    if expected_revision is None or expected_revision != max((entry.id for entry in latest.values()), default=0):
        raise ValidationError("Row exclusions changed. Review them before saving.")
    records = ForecastDataRecord.query.filter(
        ForecastDataRecord.extract_id == extract_id, ForecastDataRecord.id.in_(record_ids),
    ).all()
    if len(records) != len(record_ids):
        raise ValidationError("Every selected row must belong to this extract.")
    for record in records:
        active = record.id in latest and latest[record.id].action == "exclude"
        if active == (action == "exclude"):
            raise ValidationError("A selected row is already excluded or not excluded. Review your selection.")
    for record in records:
        entry = ForecastRowExclusion(
            extract_id=extract_id, record_id=record.id, action=action,
            rationale=rationale, created_by_id=user_id,
        )
        db.session.add(entry)
        db.session.flush()
        db.session.add(AuditLog(
            user_id=user_id, action="forecast_row_exclusion_saved", resource="forecast_data_extract",
            details=json.dumps({"extract_id": extract_id, "row_exclusion_id": entry.id, "record_id": record.id, "action": action}),
        ))
    db.session.commit()


def finding_adjustment_counts(extract_id, latest, row_exclusions):
    """Count flagged rows covered by active adjustments, once per finding."""
    active = {key: entry for key, entry in latest.items() if entry.action != "clear"}
    order_wide = {
        key[0] for key, entry in active.items()
        if entry.corrections.get("order_date_scope") == "order"
    }
    orders = sorted({key[0] for key in active})
    counts = Counter()
    covered_ids = set()
    for offset in range(0, len(orders), 400):
        rows = ForecastDataRecord.query.filter(
            ForecastDataRecord.extract_id == extract_id,
            ForecastDataRecord.has_findings.is_(True),
            ForecastDataRecord.data["order_num"].as_integer().in_(orders[offset:offset + 400]),
        ).with_entities(ForecastDataRecord.id, ForecastDataRecord.data, ForecastDataRecord.findings).yield_per(500)
        for record_id, data, findings in rows:
            entry = active.get(_key(data))
            covered = data["order_num"] in order_wide
            if entry:
                covered = covered or entry.action == "exclude" or any(
                    field in entry.corrections for field in ("void_line", "order_date", "req_date")
                ) or (
                    data["assembly_seq"] in (None, 0)
                    and any(field in entry.corrections for field in ("release_qty", "release_price_gbp"))
                )
            if covered:
                counts.update(set(findings))
                covered_ids.add(record_id)
    excluded = [record_id for record_id, entry in row_exclusions.items()
                if entry.action == "exclude" and record_id not in covered_ids]
    for offset in range(0, len(excluded), 400):
        for findings, in ForecastDataRecord.query.filter(
            ForecastDataRecord.extract_id == extract_id,
            ForecastDataRecord.id.in_(excluded[offset:offset + 400]),
        ).with_entities(ForecastDataRecord.findings):
            counts.update(set(findings))
    return counts


def adjustment_suggestions(extract, records, current):
    keys = {_key(record.data) for record in records if None not in _key(record.data)}
    if not keys:
        return {}
    previous = ForecastReleaseAdjustment.query.join(ForecastDataExtract).filter(
        ForecastDataExtract.id < extract.id,
        ForecastDataExtract.source == extract.source,
        ForecastDataExtract.rules_version == extract.rules_version,
        ForecastReleaseAdjustment.order_num.in_({key[0] for key in keys}),
    ).order_by(ForecastDataExtract.id.desc(), ForecastReleaseAdjustment.id.desc()).all()
    signatures = {}
    suggestions = {}
    seen = set()
    for entry in previous:
        key = (entry.order_num, entry.order_line, entry.rel_num)
        if key not in keys or key in current or (entry.extract_id, key) in seen:
            continue
        seen.add((entry.extract_id, key))
        prior = db.session.get(ForecastDataExtract, entry.extract_id)
        if prior.source_settings.get("capture_environment") != extract.source_settings.get("capture_environment"):
            continue
        if key[0] not in signatures:
            signatures[key[0]] = order_signature((row.data, row.findings) for row in _order_rows(extract.id, key[0]))
        if entry.order_signature == signatures[key[0]]:
            if entry.action != "clear":
                suggestions[key] = entry
            keys.remove(key)
    return suggestions


def _corrections(values):
    result = {}
    for field in ADJUSTABLE:
        value = values.get(field, "").strip()
        if not value:
            continue
        if field in ("order_date", "req_date"):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ValidationError(f"Enter a valid {field.replace('_', ' ')}.") from exc
            result[field] = parsed.isoformat()
        elif field == "void_line":
            if value not in ("true", "false"):
                raise ValidationError("Choose a valid void status.")
            result[field] = value == "true"
        else:
            try:
                parsed = Decimal(value)
            except InvalidOperation as exc:
                raise ValidationError(f"Enter a valid {field.replace('_', ' ')}.") from exc
            scale = 3 if field == "release_qty" else 4
            if not parsed.is_finite() or abs(parsed) >= Decimal(10) ** (12 - scale if field == "release_qty" else 14 - scale):
                raise ValidationError(f"{field.replace('_', ' ')} is outside the supported range.")
            if parsed.as_tuple().exponent < -scale:
                raise ValidationError(f"{field.replace('_', ' ')} has too many decimal places.")
            result[field] = format(parsed, "f")
    if "order_date" in result:
        scope = values.get("order_date_scope", "")
        if scope not in ("release", "order"):
            raise ValidationError("Choose whether the order date applies to this release or the whole captured order.")
        result["order_date_scope"] = scope
    return result


def save_adjustment(record_id, action, values, rationale, user_id, identity, expected_id, reused_from_id=None):
    record = db.session.get(ForecastDataRecord, record_id)
    if record is None:
        raise ValidationError("The saved row no longer exists.")
    extract = _lock_extract(record.extract_id, identity)
    if extract.derived_from_id is not None:
        raise ValidationError("Make adjustments on the original extract, not an adjusted extract.")
    key = _key(record.data)
    if None in key:
        raise ValidationError("A complete order, line and release is required.")
    if action not in ("correct", "exclude", "clear"):
        raise ValidationError("Choose an adjustment action.")
    rationale = rationale.strip()
    if not rationale or len(rationale) > 2000:
        raise ValidationError("Provide evidence between 1 and 2,000 characters.")
    if action != "correct" and any(values.get(field, "").strip() for field in ADJUSTABLE):
        raise ValidationError("Remove corrected values or choose the correction action.")
    corrections = _corrections(values) if action == "correct" else {}
    if action == "correct" and not corrections:
        raise ValidationError("Enter at least one corrected value.")
    current = latest_adjustments(extract.id).get(key)
    if expected_id is None or expected_id != (current.id if current else 0):
        raise ValidationError("This adjustment has changed. Reopen the row before saving.")
    rows = _order_rows(extract.id, key[0])
    signature = order_signature((row.data, row.findings) for row in rows)
    if reused_from_id is not None:
        suggestion = adjustment_suggestions(extract, [record], {})
        prior = suggestion.get(key)
        if prior is None or prior.id != reused_from_id or prior.action != action or prior.corrections != corrections:
            raise ValidationError("The suggested adjustment has changed. Review it again.")
    entry = ForecastReleaseAdjustment(
        extract_id=extract.id, order_num=key[0], order_line=key[1], rel_num=key[2],
        action=action, corrections=corrections, rationale=rationale,
        order_signature=signature, reused_from_id=reused_from_id, created_by_id=user_id,
    )
    db.session.add(entry)
    db.session.flush()
    db.session.add(AuditLog(
        user_id=user_id, action="forecast_release_adjustment_saved", resource="forecast_data_extract",
        details=json.dumps({"extract_id": extract.id, "adjustment_id": entry.id, "action": action}),
    ))
    db.session.commit()
    return entry


def _carry_unchanged_reviews(source, extract, user_id):
    source_row = aliased(ForecastDataRecord)
    target_row = aliased(ForecastDataRecord)
    latest = db.session.query(
        ForecastDataDecision.record_id, func.max(ForecastDataDecision.id).label("decision_id"),
    ).join(source_row, source_row.id == ForecastDataDecision.record_id).filter(
        source_row.extract_id == source.id,
    ).group_by(ForecastDataDecision.record_id).subquery()
    candidates = db.session.query(
        source_row.data, target_row.id, ForecastDataDecision,
    ).join(latest, latest.c.record_id == source_row.id).join(
        ForecastDataDecision, ForecastDataDecision.id == latest.c.decision_id,
    ).join(target_row, (target_row.extract_id == extract.id)
           & (target_row.source_row_id == source_row.source_row_id)).filter(
        source_row.extract_id == source.id, target_row.has_findings.is_(True),
        ~target_row.decisions.any(),
    ).all()
    by_order = defaultdict(list)
    for data, target_id, prior in candidates:
        if data.get("order_num") is not None:
            by_order[data["order_num"]].append((target_id, prior))
    orders = sorted(by_order)
    carried = 0
    for offset in range(0, len(orders), 400):
        order_batch = orders[offset:offset + 400]
        observed = {source.id: {}, extract.id: {}}
        rows = db.session.query(
            ForecastDataRecord.extract_id, ForecastDataRecord.source_row_id,
            ForecastDataRecord.data, ForecastDataRecord.findings,
        ).filter(
            ForecastDataRecord.extract_id.in_((source.id, extract.id)),
            ForecastDataRecord.data["order_num"].as_integer().in_(order_batch),
        ).yield_per(500)
        for extract_id, source_row_id, data, findings in rows:
            order = data["order_num"]
            observed[extract_id].setdefault(order, {})[source_row_id] = _row_signature(data, findings)
        unchanged = {
            order for order in order_batch
            if observed[source.id].get(order) and observed[source.id].get(order) == observed[extract.id].get(order)
        }
        for order in unchanged:
            for target_id, prior in by_order[order]:
                decision = _append_data_decision(
                    extract.id, target_id, prior.status, prior.rationale, user_id,
                    remember=prior.remember, reused_from_id=prior.id, carried_forward=True,
                )
                decision.reviewed_by_id = prior.reviewed_by_id
                decision.reviewed_at = prior.reviewed_at
                carried += 1
    return carried


def backfill_adjusted_reviews(extract_id, user_id, identity):
    extract = db.session.get(ForecastDataExtract, extract_id)
    if extract is None or extract.derived_from_id is None:
        raise ValidationError("Choose an existing adjusted extract.")
    source = _lock_extract(extract.derived_from_id, None)
    extract = _lock_extract(extract_id, identity)
    db.session.expire_all()
    if extract.derived_from_id != source.id:
        raise ValidationError("The adjusted extract's source has changed.")
    count = _carry_unchanged_reviews(source, extract, user_id)
    db.session.commit()
    return count


def create_adjusted_extract(extract_id, identity, expected_revision, user_id, *, expected_row_revision=0):
    source = _lock_extract(extract_id, identity)
    if source.derived_from_id is not None:
        raise ValidationError("Create an adjusted extract from the original capture.")
    if not set(SNAPSHOT_FIELDS).issubset(source.source_settings.get("fields", ())):
        raise ValidationError("This capture lacks fields required to recalculate findings.")
    latest = latest_adjustments(source.id)
    if expected_revision != max((entry.id for entry in latest.values()), default=0):
        raise ValidationError("Adjustments changed. Review them before creating an adjusted extract.")
    active = {key: entry for key, entry in latest.items() if entry.action != "clear"}
    latest_rows = latest_row_exclusions(source.id)
    if expected_row_revision != max((entry.id for entry in latest_rows.values()), default=0):
        raise ValidationError("Row exclusions changed. Review them before creating an adjusted extract.")
    excluded_rows = {record_id: entry for record_id, entry in latest_rows.items() if entry.action == "exclude"}
    if not active and not excluded_rows:
        raise ValidationError("Record at least one adjustment before creating an adjusted extract.")

    order_dates = {}
    for key, entry in active.items():
        if "order_date" in entry.corrections and entry.corrections["order_date_scope"] == "order":
            value = entry.corrections["order_date"]
            if key[0] in order_dates and order_dates[key[0]] != value:
                raise ValidationError(f"Order {key[0]} has conflicting order-wide dates.")
            order_dates[key[0]] = value
    for key, entry in active.items():
        if ("order_date" in entry.corrections and entry.corrections["order_date_scope"] == "release"
                and key[0] in order_dates and entry.corrections["order_date"] != order_dates[key[0]]):
            raise ValidationError(f"Order {key[0]} has conflicting order-wide and release dates.")

    settings = {
        **source.source_settings,
        "origin": "adjusted_saved_extract",
        "selection": "Original capture selection, with documented adjustments",
        "derived_from": source.id,
        "derived_from_hash": source.content_hash,
        "adjustment_ids": sorted(entry.id for entry in active.values()),
        "row_exclusion_ids": sorted(entry.id for entry in excluded_rows.values()),
        "adjusted_at": datetime.now(timezone.utc).isoformat(),
    }
    extract = ForecastDataExtract(
        captured_at=source.captured_at, captured_by_id=user_id, source=source.source,
        rules_version=source.rules_version, row_count=0, content_hash="",
        source_settings=settings, summary={}, derived_from_id=source.id,
    )
    db.session.add(extract)
    db.session.flush()
    digest = hashlib.sha256()
    digest.update(json.dumps({
        "source": extract.source, "rules_version": extract.rules_version,
        "source_settings": settings,
    }, sort_keys=True, separators=(",", ":")).encode())
    source_digest = hashlib.sha256()
    source_digest.update(json.dumps({
        "source": source.source, "rules_version": source.rules_version,
        "source_settings": source.source_settings,
    }, sort_keys=True, separators=(",", ":")).encode())
    query = ForecastDataRecord.query.filter_by(extract_id=source.id).with_entities(
        ForecastDataRecord.id, ForecastDataRecord.source_row_id, ForecastDataRecord.data, ForecastDataRecord.findings,
    ).order_by(*(
        ForecastDataRecord.data[field].as_integer() for field in KEY_FIELDS
    ), ForecastDataRecord.id).yield_per(500)
    counts = Counter()
    pending = []
    row_count = flagged = release_count = order_count = unassigned = 0
    observed = 0
    seen_keys = set()
    seen_excluded = set()
    for order_num, order_iter in groupby(query, key=lambda row: row.data["order_num"]):
        original = list(order_iter)
        signature = order_signature((row.data, row.findings) for row in original)
        rows = []
        for item in original:
            observed += 1
            source_digest.update(b"\n")
            source_digest.update(json.dumps(item.data, sort_keys=True, separators=(",", ":")).encode())
            data = dict(item.data)
            if not set(SNAPSHOT_FIELDS).issubset(data):
                raise ValidationError("A saved row lacks fields required to recalculate findings.")
            key = _key(data)
            entry = active.get(key)
            if entry:
                seen_keys.add(key)
                if entry.order_signature != signature:
                    raise ValidationError("The captured order no longer matches its adjustment.")
            if item.id in excluded_rows:
                seen_excluded.add(item.id)
                continue
            if entry:
                if entry.action == "exclude":
                    continue
            if order_num in order_dates:
                data["order_date"] = order_dates[order_num]
            if entry and entry.action == "correct":
                for field, value in entry.corrections.items():
                    if field == "order_date_scope":
                        continue
                    if field in ("release_qty", "release_price_gbp") and data["assembly_seq"] not in (None, 0):
                        continue
                    data[field] = value
            for field in DATE_FIELDS:
                if data[field] is not None:
                    data[field] = date.fromisoformat(data[field])
            for field in NUMBER_FIELDS:
                if data[field] is not None:
                    data[field] = Decimal(data[field])
            rows.append((item.source_row_id, data))
        if not rows:
            continue
        order_count += 1
        received = {data["order_received_date"] for _, data in rows if data["order_received_date"] is not None}
        for key_tail, members in groupby(rows, key=lambda pair: (pair[1]["order_line"], pair[1]["rel_num"])):
            release_rows = list(members)
            data_rows = [data for _, data in release_rows]
            key = (order_num, *key_tail)
            entry = active.get(key)
            if entry and any(field in entry.corrections for field in ("release_qty", "release_price_gbp", "void_line")):
                if not any(row["assembly_seq"] in (None, 0) for row in data_rows):
                    raise ValidationError(f"Release {key} has no main row to correct.")
            release_count += 1
            common = _release_findings(data_rows)
            if source.order_period:
                ordered = release_order_date([(row["order_date"], row["assembly_seq"] in (None, 0)) for row in data_rows])
                if ordered is None:
                    unassigned += 1
                    common.append("unassigned_order_date")
                main = [row for row in data_rows if row["assembly_seq"] in (None, 0)]
                for field, rule in (("release_price_gbp", "invalid_weekly_gbp"), ("void_line", "invalid_weekly_void")):
                    values = {row[field] for row in main}
                    if main and (None in values or len(values) != 1):
                        common.append(rule)
            if len(received) > 1:
                common.append("conflicting_received")
            for source_row_id, row in release_rows:
                findings = common + _row_findings(row)
                data = {field: _serialize(row[field]) for field in SNAPSHOT_FIELDS}
                digest.update(b"\n")
                digest.update(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
                pending.append({"extract_id": extract.id, "source_row_id": source_row_id, "data": data,
                                "findings": findings, "has_findings": bool(findings)})
                row_count += 1
                flagged += bool(findings)
                counts.update(findings)
                if len(pending) >= 500:
                    db.session.execute(insert(ForecastDataRecord), pending)
                    pending.clear()
    if seen_keys != active.keys():
        raise ValidationError("An adjusted release is missing from the original capture.")
    if seen_excluded != excluded_rows.keys():
        raise ValidationError("An excluded row is missing from the original capture.")
    if observed != source.row_count or source_digest.hexdigest() != source.content_hash:
        raise ValidationError("The original capture no longer matches its saved identity.")
    if not row_count:
        raise ValidationError("The adjustments exclude every saved row.")
    if pending:
        db.session.execute(insert(ForecastDataRecord), pending)
    extract.row_count = row_count
    extract.content_hash = digest.hexdigest()
    extract.summary = {
        "flagged_rows": flagged, "release_count": release_count, "order_count": order_count,
        "rules": source.summary["rules"], "counts": dict(counts),
    }
    if source.order_period:
        extract.summary["unassigned_releases"] = unassigned
    _carry_unchanged_reviews(source, extract, user_id)
    db.session.add(AuditLog(
        user_id=user_id, action="forecast_adjusted_extract_saved", resource="forecast_data_extract",
        details=json.dumps({"extract_id": extract.id, "source_extract_id": source.id,
                            "adjustment_ids": settings["adjustment_ids"], "content_hash": extract.content_hash}),
    ))
    db.session.commit()
    return extract
