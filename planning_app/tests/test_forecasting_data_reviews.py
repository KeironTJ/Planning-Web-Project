import csv
import hashlib
import html
import json
import re
import runpy
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql

from app.auth.models import AuditLog
from app.core.exceptions import NotFoundError, ValidationError
from app.forecasting.adjustments import (
    adjustment_suggestions,
    backfill_adjusted_reviews,
    create_adjusted_extract,
    finding_adjustment_counts,
    latest_adjustments,
    latest_row_exclusions,
    row_exclusion_suggestions,
    save_adjustment,
    save_row_exclusions,
)
from app.forecasting.data_quality import (
    finding_matches,
    save_data_decision,
    save_va05_extract,
)
from app.forecasting.models import (
    ForecastDataDecision,
    ForecastDataExtract,
    ForecastDataNote,
    ForecastDataRecord,
    ForecastFeatureDecision,
    ForecastRowExclusion,
)
from app.forecasting.services import (
    save_model_category,
    save_model_classification,
    save_model_classification_updates,
)
from app.forecasting.weekly_intake import (
    REQUIRED_FIELDS,
    IntakeWeek,
    explore_weekly_intake,
    financial_year_start,
    score_forecasts,
    summarize_time_patterns,
    summarize_weekly_distribution,
)
from app.sales.orders.models import SalesOrder
from scripts.weekly_intake_review import (
    ForecastComparison,
    compare_lag_regression,
    compare_forecasts,
    describe_mix,
    describe_weekly_intake,
    previous_week_benchmark,
    report_lag_experiment,
    review,
)

from .conftest import login


PAGE = "/ai-tools/data-readiness"


def add_source(session, **overrides):
    values = {
        "order_num": 12,
        "order_line": 1,
        "rel_num": 1,
        "assembly_seq": 0,
        "job_num": "J1",
        "order_received_date": date(2026, 1, 2),
        "order_date": date(2026, 1, 2),
        "req_date": date(2026, 2, 1),
        "release_qty": Decimal("3"),
        "release_price_gbp": Decimal("120"),
        "ium": "EA",
        "currency_code": "GBP",
    }
    values.update(overrides)
    row = SalesOrder(**values)
    session.add(row)
    session.commit()
    return row


def test_adjusted_extract_preserves_source_and_uses_release_grain(db_session, planner_user, client):
    add_source(db_session, void_line=False)
    add_source(db_session, assembly_seq=1, job_num="J2", release_price_gbp=Decimal("999"), void_line=False)
    add_source(db_session, rel_num=2, job_num="J3", release_price_gbp=Decimal("40"), void_line=False)
    add_source(db_session, order_num=13, job_num="J4", void_line=False)
    source = save_va05_extract(planner_user.id, period=(date(2026, 1, 1), date(2026, 1, 31)))
    rows = ForecastDataRecord.query.filter_by(extract_id=source.id).order_by(ForecastDataRecord.id).all()
    before = [(row.data.copy(), list(row.findings)) for row in rows]
    corrected = save_adjustment(
        rows[0].id, "correct",
        {"release_qty": "5", "release_price_gbp": "200", "void_line": "false",
         "order_date": "2026-01-05", "order_date_scope": "order", "req_date": "2026-03-01"},
        "Verified against historical invoice.", planner_user.id, source.capture_identity, 0,
    )
    excluded = save_adjustment(
        rows[3].id, "exclude", {}, "Duplicate import, verified.", planner_user.id, source.capture_identity, 0,
    )
    derived = create_adjusted_extract(source.id, source.capture_identity, excluded.id, planner_user.id)
    result = ForecastDataRecord.query.filter_by(extract_id=derived.id).order_by(ForecastDataRecord.id).all()
    assert [(row.data, row.findings) for row in rows] == before
    assert derived.derived_from_id == source.id
    assert derived.source_settings["derived_from_hash"] == source.content_hash
    assert derived.source_settings["adjustment_ids"] == [corrected.id, excluded.id]
    assert derived.content_hash != source.content_hash
    assert len(result) == 3
    main, assembly, other_release = [row.data for row in result]
    assert (main["release_qty"], main["release_price_gbp"], main["req_date"]) == ("5", "200", "2026-03-01")
    assert (assembly["release_qty"], assembly["release_price_gbp"], assembly["req_date"]) == ("3.000", "999.0000", "2026-03-01")
    assert {row.data["order_date"] for row in result} == {"2026-01-05"}
    assert other_release["release_price_gbp"] == "40.0000"
    profile = explore_weekly_intake([row.data for row in result], date(2026, 2, 1))
    assert profile.weeks[0].net_gbp == Decimal("240")

    save_adjustment(rows[0].id, "clear", {}, "Superseded.", planner_user.id, source.capture_identity, corrected.id)
    login(client, planner_user.email, "Planner!Pass1234")
    page = client.get(f"{PAGE}/extracts/{derived.id}")
    assert page.status_code == 200
    assert b"Verified against historical invoice." in page.data
    assert b"Duplicate import, verified." in page.data
    library = client.get(PAGE).get_data(as_text=True)
    assert "Latest saved extract" in library
    assert "Latest capture" not in library


def test_adjustment_suggestions_require_unchanged_order_and_confirmation(db_session, planner_user):
    sales = add_source(db_session, void_line=False)
    old = save_va05_extract(planner_user.id)
    old_row = ForecastDataRecord.query.filter_by(extract_id=old.id).one()
    original = save_adjustment(old_row.id, "exclude", {}, "Verified duplicate.", planner_user.id, old.capture_identity, 0)
    new = save_va05_extract(planner_user.id)
    row = ForecastDataRecord.query.filter_by(extract_id=new.id).one()
    key = (12, 1, 1)
    assert adjustment_suggestions(new, [row], {})[key].id == original.id
    assert latest_adjustments(new.id) == {}
    with pytest.raises(ValidationError, match="suggested adjustment has changed"):
        save_adjustment(row.id, "correct", {"release_qty": "4"}, "Different", planner_user.id,
                        new.capture_identity, 0, reused_from_id=original.id)
    db_session.rollback()
    confirmed = save_adjustment(row.id, "exclude", {}, original.rationale, planner_user.id,
                                new.capture_identity, 0, reused_from_id=original.id)
    assert confirmed.reused_from_id == original.id
    with pytest.raises(ValidationError, match="changed"):
        save_adjustment(row.id, "clear", {}, "Stale edit", planner_user.id, new.capture_identity, 0)
    db_session.rollback()
    sales.release_qty = Decimal("8")
    db_session.commit()
    changed = save_va05_extract(planner_user.id)
    changed_row = ForecastDataRecord.query.filter_by(extract_id=changed.id).one()
    assert adjustment_suggestions(changed, [changed_row], {}) == {}


def test_suggestions_prefer_latest_capture_and_respect_cleared_decisions(db_session, planner_user):
    add_source(db_session)
    old = save_va05_extract(planner_user.id)
    old_row = ForecastDataRecord.query.filter_by(extract_id=old.id).one()
    initial = save_adjustment(old_row.id, "exclude", {}, "Old evidence", planner_user.id, old.capture_identity, 0)
    newer = save_va05_extract(planner_user.id)
    newer_row = ForecastDataRecord.query.filter_by(extract_id=newer.id).one()
    recent = save_adjustment(newer_row.id, "clear", {}, "No longer excluded", planner_user.id, newer.capture_identity, 0)
    save_adjustment(old_row.id, "exclude", {}, "Later edit of older capture", planner_user.id, old.capture_identity, initial.id)
    current = save_va05_extract(planner_user.id)
    current_row = ForecastDataRecord.query.filter_by(extract_id=current.id).one()
    assert adjustment_suggestions(current, [current_row], {}) == {}
    save_adjustment(newer_row.id, "correct", {"release_qty": "4"}, "New evidence",
                    planner_user.id, newer.capture_identity, recent.id)
    assert adjustment_suggestions(current, [current_row], {})[(12, 1, 1)].rationale == "New evidence"


def test_adjustment_conflicts_and_empty_result_are_rejected(db_session, planner_user):
    add_source(db_session)
    add_source(db_session, rel_num=2, job_num="J2")
    source = save_va05_extract(planner_user.id)
    first, second = ForecastDataRecord.query.filter_by(extract_id=source.id).order_by(ForecastDataRecord.id).all()
    one = save_adjustment(first.id, "correct", {"order_date": "2026-01-05", "order_date_scope": "order"},
                          "Evidence", planner_user.id, source.capture_identity, 0)
    two = save_adjustment(second.id, "correct", {"order_date": "2026-01-12", "order_date_scope": "release"},
                          "Evidence", planner_user.id, source.capture_identity, 0)
    with pytest.raises(ValidationError, match="conflicting"):
        create_adjusted_extract(source.id, source.capture_identity, two.id, planner_user.id)
    db_session.rollback()
    one = save_adjustment(first.id, "exclude", {}, "Evidence", planner_user.id, source.capture_identity, one.id)
    two = save_adjustment(second.id, "exclude", {}, "Evidence", planner_user.id, source.capture_identity, two.id)
    with pytest.raises(ValidationError, match="exclude every"):
        create_adjusted_extract(source.id, source.capture_identity, two.id, planner_user.id)
    db_session.rollback()
    assert ForecastDataExtract.query.count() == 1


def test_adjustment_rejects_missing_main_and_source_tampering(db_session, planner_user):
    add_source(db_session, assembly_seq=1)
    source = save_va05_extract(planner_user.id)
    row = ForecastDataRecord.query.filter_by(extract_id=source.id).one()
    entry = save_adjustment(row.id, "correct", {"release_qty": "5"}, "Evidence",
                            planner_user.id, source.capture_identity, 0)
    with pytest.raises(ValidationError, match="no main row"):
        create_adjusted_extract(source.id, source.capture_identity, entry.id, planner_user.id)
    db_session.rollback()
    row.data = {**row.data, "release_qty": "7"}
    db_session.commit()
    with pytest.raises(ValidationError, match="no longer matches"):
        create_adjusted_extract(source.id, source.capture_identity, entry.id, planner_user.id)
    db_session.rollback()
    assert ForecastDataExtract.query.count() == 1


def test_adjustment_recalculates_findings_and_release_only_date(db_session, planner_user):
    add_source(db_session, release_qty=Decimal("0"), release_price_gbp=Decimal("0"),
               req_date=None, void_line=True)
    add_source(db_session, rel_num=2, job_num="J2", void_line=False)
    source = save_va05_extract(planner_user.id, period=(date(2026, 1, 1), date(2026, 1, 31)))
    first, second = ForecastDataRecord.query.filter_by(extract_id=source.id).order_by(ForecastDataRecord.id).all()
    assert "nonpositive_qty" in first.findings
    change = save_adjustment(
        first.id, "correct",
        {"release_qty": "5", "release_price_gbp": "100", "void_line": "false",
         "req_date": "2026-03-01", "order_date": "2026-01-05", "order_date_scope": "release"},
        "Verified.", planner_user.id, source.capture_identity, 0,
    )
    with pytest.raises(ValidationError, match="changed"):
        create_adjusted_extract(source.id, source.capture_identity, 0, planner_user.id)
    db_session.rollback()
    derived = create_adjusted_extract(source.id, source.capture_identity, change.id, planner_user.id)
    result = ForecastDataRecord.query.filter_by(extract_id=derived.id).order_by(ForecastDataRecord.id).all()
    assert "nonpositive_qty" not in result[0].findings
    assert result[0].data["void_line"] is False
    assert result[0].data["order_date"] == "2026-01-05"
    assert result[1].data["order_date"] == second.data["order_date"]
    assert "nonpositive_qty" in first.findings


def test_finding_planned_rows_count_active_adjustment_scope(db_session, planner_user, client):
    add_source(db_session, order_received_date=None, release_qty=Decimal("0"))
    add_source(db_session, order_received_date=None, assembly_seq=1, job_num="J2")
    add_source(db_session, order_received_date=None, rel_num=2, job_num="J3")
    add_source(db_session, order_num=13, order_received_date=None, job_num="J4")
    add_source(db_session, order_num=13, order_received_date=None, assembly_seq=1, job_num="J5")
    add_source(db_session, order_num=14, order_received_date=None, job_num="J6")
    source = save_va05_extract(planner_user.id)
    rows = ForecastDataRecord.query.filter_by(extract_id=source.id).order_by(ForecastDataRecord.id).all()
    login(client, planner_user.email, "Planner!Pass1234")

    def planned_count(rule):
        page = client.get(f"{PAGE}/extracts/{source.id}").get_data(as_text=True)
        row = page.split(f'data-finding-progress="{rule}"', 1)[1].split("</tr>", 1)[0]
        match = re.search(r'data-planned-rows>([\d,]+)</td>', row)
        assert match is not None
        return int(match.group(1).replace(",", ""))

    assert planned_count("missing_received") == 0
    change = save_adjustment(
        rows[0].id, "correct", {"release_qty": "5"}, "Evidence",
        planner_user.id, source.capture_identity, 0,
    )
    assert planned_count("missing_received") == 1
    assert planned_count("nonpositive_qty") == 1
    change = save_adjustment(
        rows[0].id, "correct",
        {"release_qty": "5", "order_date": "2026-01-05", "order_date_scope": "order"},
        "Evidence", planner_user.id, source.capture_identity, change.id,
    )
    exclusion = save_adjustment(
        rows[3].id, "exclude", {}, "Evidence", planner_user.id, source.capture_identity, 0,
    )
    assert planned_count("missing_received") == 5
    assert planned_count("nonpositive_qty") == 1

    derived = create_adjusted_extract(source.id, source.capture_identity, exclusion.id, planner_user.id)
    derived_page = client.get(f"{PAGE}/extracts/{derived.id}").get_data(as_text=True)
    assert "Planned rows" not in derived_page
    assert "data-planned-rows" not in derived_page

    save_adjustment(
        rows[0].id, "clear", {}, "No longer needed",
        planner_user.id, source.capture_identity, change.id,
    )
    assert planned_count("missing_received") == 2
    save_adjustment(
        rows[3].id, "correct", {"release_qty": "5"}, "Replaced exclusion",
        planner_user.id, source.capture_identity, exclusion.id,
    )
    assert planned_count("missing_received") == 1


def test_selected_row_exclusions_preserve_other_rows_and_provenance(db_session, planner_user, client):
    add_source(db_session, release_qty=Decimal("0"), release_price_gbp=Decimal("0"))
    add_source(db_session, release_qty=Decimal("3"), release_price_gbp=Decimal("120"), job_num="J2")
    add_source(db_session, assembly_seq=1, job_num="J3")
    source = save_va05_extract(planner_user.id)
    rows = ForecastDataRecord.query.filter_by(extract_id=source.id).order_by(ForecastDataRecord.id).all()
    before = [(row.data.copy(), list(row.findings)) for row in rows]
    assert "conflicting_qty" in rows[0].findings

    login(client, planner_user.email, "Planner!Pass1234")
    path = f"{PAGE}/extracts/{source.id}"
    response = client.post(f"{path}/row-exclusions", data={
        "capture_identity": source.capture_identity, "expected_revision": "0",
        "action": "exclude", "record_id": [str(rows[0].id), str(rows[2].id)],
        "rationale": "Verified duplicate observations.", "confirm": "yes",
        "scope": "all", "release": str(rows[0].id), "page": "1", "per_page": "50",
    })
    assert response.status_code == 302
    redirect_url = urlsplit(response.location)
    assert parse_qs(redirect_url.query) == {
        "scope": ["all"], "release": [str(rows[0].id)], "page": ["1"], "per_page": ["50"],
    }
    assert redirect_url.fragment == "saved-records"
    latest = latest_row_exclusions(source.id)
    assert set(latest) == {rows[0].id, rows[2].id}
    revision = max(entry.id for entry in latest.values())
    page = client.get(path).get_data(as_text=True)
    assert "2 rows active" in page
    assert "Row exclusion planned" in page
    assert finding_adjustment_counts(source.id, {}, latest)["conflicting_qty"] == 2
    correction = save_adjustment(
        rows[1].id, "correct", {"req_date": "2026-03-01"}, "Verified.",
        planner_user.id, source.capture_identity, 0,
    )
    assert finding_adjustment_counts(source.id, latest_adjustments(source.id), latest)["conflicting_qty"] == 3
    derived = create_adjusted_extract(
        source.id, source.capture_identity, correction.id, planner_user.id, expected_row_revision=revision,
    )
    remaining = ForecastDataRecord.query.filter_by(extract_id=derived.id).all()
    assert len(remaining) == 1
    assert remaining[0].source_row_id == rows[1].source_row_id
    assert "conflicting_qty" not in remaining[0].findings
    assert [(row.data, row.findings) for row in rows] == before
    assert derived.source_settings["row_exclusion_ids"] == sorted(entry.id for entry in latest.values())
    save_row_exclusions(
        source.id, [rows[0].id], "clear", "Restore this row.", planner_user.id,
        source.capture_identity, revision,
    )
    derived_page = client.get(f"{PAGE}/extracts/{derived.id}").get_data(as_text=True)
    assert "Verified duplicate observations." in derived_page
    assert f"Excluded saved row {rows[0].id}" in derived_page
    assert latest_row_exclusions(source.id)[rows[0].id].action == "clear"


def test_row_exclusion_batch_is_atomic_and_rejects_stale_or_invalid_requests(
    db_session, planner_user, viewer_user, client,
):
    add_source(db_session, release_qty=Decimal("0"))
    add_source(db_session, rel_num=2, job_num="J2")
    source = save_va05_extract(planner_user.id)
    rows = ForecastDataRecord.query.filter_by(extract_id=source.id).order_by(ForecastDataRecord.id).all()
    save_row_exclusions(source.id, [rows[0].id], "exclude", "Evidence.", planner_user.id, source.capture_identity, 0)
    latest = latest_row_exclusions(source.id)
    revision = latest[rows[0].id].id
    for selected, action, expected in (
        ([rows[1].id], "exclude", 0),
        ([rows[0].id, rows[1].id], "exclude", revision),
        ([rows[1].id, 99999999], "exclude", revision),
        ([rows[1].id], "clear", revision),
    ):
        with pytest.raises(ValidationError):
            save_row_exclusions(source.id, selected, action, "Evidence.", planner_user.id,
                                source.capture_identity, expected)
        db_session.rollback()
    assert set(latest_row_exclusions(source.id)) == {rows[0].id}
    with pytest.raises(ValidationError, match="Row exclusions changed"):
        create_adjusted_extract(source.id, source.capture_identity, 0, planner_user.id)
    db_session.rollback()
    login(client, viewer_user.email, "Viewer!Pass1234")
    assert client.post(f"{PAGE}/extracts/{source.id}/row-exclusions", data={
        "action": "exclude", "record_id": str(rows[1].id), "confirm": "yes",
        "rationale": "Evidence.", "capture_identity": source.capture_identity,
        "expected_revision": revision,
    }).status_code == 403
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(f"{PAGE}/extracts/{source.id}/row-exclusions", data={
        "action": "exclude", "record_id": str(rows[1].id),
        "rationale": "Evidence.", "capture_identity": source.capture_identity,
        "expected_revision": revision,
    }, follow_redirects=True)
    assert b"Confirm the selected rows" in response.data
    assert set(latest_row_exclusions(source.id)) == {rows[0].id}
    for invalid_id in ("0", str(2**63), "9" * 5000):
        response = client.post(f"{PAGE}/extracts/{source.id}/row-exclusions", data={
            "action": "exclude", "record_id": invalid_id, "confirm": "yes",
            "rationale": "Evidence.", "capture_identity": source.capture_identity,
            "expected_revision": revision,
        }, follow_redirects=True)
        assert b"Choose valid saved rows" in response.data
        assert set(latest_row_exclusions(source.id)) == {rows[0].id}


def test_excluding_every_row_cannot_create_empty_adjusted_extract(db_session, planner_user):
    add_source(db_session)
    source = save_va05_extract(planner_user.id)
    row = ForecastDataRecord.query.filter_by(extract_id=source.id).one()
    save_row_exclusions(source.id, [row.id], "exclude", "Evidence.", planner_user.id, source.capture_identity, 0)
    revision = latest_row_exclusions(source.id)[row.id].id
    with pytest.raises(ValidationError, match="exclude every saved row"):
        create_adjusted_extract(source.id, source.capture_identity, 0, planner_user.id, expected_row_revision=revision)
    db_session.rollback()
    assert ForecastDataExtract.query.filter_by(derived_from_id=source.id).count() == 0


def test_adjusted_extract_carries_only_unchanged_order_reviews_with_provenance(
    db_session, planner_user, admin_user, client,
):
    for order, release in ((12, 1), (13, 1), (13, 2), (14, 1), (15, 1), (16, 1)):
        add_source(db_session, order_num=order, rel_num=release, release_qty=None)
    source = save_va05_extract(planner_user.id)
    rows = ForecastDataRecord.query.filter_by(extract_id=source.id).order_by(ForecastDataRecord.id).all()
    for row in rows[:5]:
        save_data_decision(row.id, "accepted", "Original evidence.", planner_user.id,
                           capture_identity=source.capture_identity, remember=False)
    latest = save_data_decision(rows[4].id, "investigate", "Latest evidence.", planner_user.id,
                                capture_identity=source.capture_identity, remember=True)
    correction = save_adjustment(rows[2].id, "correct", {"release_qty": "5"}, "Verified.",
                                 planner_user.id, source.capture_identity, 0)
    exclusion = save_adjustment(rows[3].id, "exclude", {}, "Verified.",
                                planner_user.id, source.capture_identity, 0)
    derived = create_adjusted_extract(source.id, source.capture_identity, exclusion.id, admin_user.id)
    targets = {row.source_row_id: row for row in ForecastDataRecord.query.filter_by(extract_id=derived.id)}
    assert rows[3].source_row_id not in targets
    assert not targets[rows[1].source_row_id].decisions
    assert not targets[rows[2].source_row_id].decisions
    carried = targets[rows[0].source_row_id].decisions[0]
    original = rows[0].decisions[0]
    assert (carried.status, carried.rationale, carried.remember, carried.reused_from_id) == (
        "accepted", "Original evidence.", False, original.id,
    )
    assert carried.reviewed_by_id == original.reviewed_by_id
    assert carried.reviewed_at == original.reviewed_at
    assert targets[rows[4].source_row_id].decisions[0].reused_from_id == latest.id
    log = AuditLog.query.filter_by(user_id=admin_user.id, action="forecast_data_decision_saved").all()
    assert len(log) == 2 and all(json.loads(entry.details)["carried_forward"] for entry in log)
    login(client, planner_user.email, "Planner!Pass1234")
    page = client.get(f"{PAGE}/extracts/{derived.id}?scope=all").get_data(as_text=True)
    assert "Reviews are carried forward" in page and "Based on" in page
    assert correction.action == "correct"


def test_adjusted_review_backfill_preserves_existing_decisions_and_is_idempotent(db_session, planner_user):
    add_source(db_session, order_num=12, release_qty=None)
    add_source(db_session, order_num=13, release_qty=None)
    add_source(db_session, order_num=14, release_qty=None)
    source = save_va05_extract(planner_user.id)
    rows = ForecastDataRecord.query.filter_by(extract_id=source.id).order_by(ForecastDataRecord.id).all()
    exclusion = save_adjustment(rows[2].id, "exclude", {}, "Verified.", planner_user.id, source.capture_identity, 0)
    derived = create_adjusted_extract(source.id, source.capture_identity, exclusion.id, planner_user.id)
    targets = {row.source_row_id: row for row in ForecastDataRecord.query.filter_by(extract_id=derived.id)}
    for row in rows[:2]:
        save_data_decision(row.id, "accepted", "Original.", planner_user.id, capture_identity=source.capture_identity)
    save_data_decision(targets[rows[0].source_row_id].id, "investigate", "Existing target.",
                       planner_user.id, capture_identity=derived.capture_identity)
    with pytest.raises(ValidationError, match="extract has changed"):
        backfill_adjusted_reviews(derived.id, planner_user.id, "stale")
    db_session.rollback()
    assert backfill_adjusted_reviews(derived.id, planner_user.id, derived.capture_identity) == 1
    assert backfill_adjusted_reviews(derived.id, planner_user.id, derived.capture_identity) == 0
    db_session.expire_all()
    assert targets[rows[0].source_row_id].decisions[0].status == "investigate"
    assert targets[rows[1].source_row_id].decisions[0].status == "accepted"
    assert targets[rows[1].source_row_id].decisions[0].reused_from_id == rows[1].decisions[0].id


def test_row_exclusion_suggestions_require_bulk_confirmation_and_keep_provenance(
    db_session, client, planner_user, viewer_user,
):
    live = [add_source(db_session, order_num=number) for number in (12, 13)]
    add_source(db_session, order_num=14)
    original = save_va05_extract(planner_user.id)
    old_rows = ForecastDataRecord.query.filter_by(extract_id=original.id).order_by(ForecastDataRecord.id).all()
    save_row_exclusions(original.id, [row.id for row in old_rows[:2]], "exclude",
                        "Checked original.", planner_user.id, original.capture_identity, 0)
    previous = latest_row_exclusions(original.id)
    for row in live:
        db_session.delete(row)
    db_session.commit()
    for number in (12, 13):
        add_source(db_session, order_num=number)
    current = save_va05_extract(planner_user.id)
    new_rows = sorted(
        ForecastDataRecord.query.filter_by(extract_id=current.id).all(), key=lambda row: row.data["order_num"],
    )
    assert all(new_rows[i].source_row_id != old_rows[i].source_row_id for i in range(2))
    expected = [f"{new_rows[i].id}:{previous[old_rows[i].id].id}" for i in range(2)]
    path = f"{PAGE}/extracts/{current.id}"
    payload = {"capture_identity": current.capture_identity, "suggestion": expected, "confirm": "yes", "scope": "all"}
    login(client, viewer_user.email, "Viewer!Pass1234")
    assert client.post(f"{path}/confirm-row-exclusions", data=payload).status_code == 403
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    page = client.get(path + "?scope=all").get_data(as_text=True)
    assert "Previously excluded: 2 suggestion(s)" in page
    assert "Checked original." in page
    assert all(value in page for value in expected)
    suggestions_page = client.get(path + "?scope=exclusion_suggestions").get_data(as_text=True)
    assert "2 matching saved records" in suggestions_page
    assert all(value in suggestions_page for value in expected)
    assert not latest_row_exclusions(current.id)
    for invalid in (
        {**payload, "confirm": ""},
        {**payload, "capture_identity": "stale"},
        {**payload, "suggestion": [expected[0], f"{new_rows[1].id}:999999"]},
        {**payload, "suggestion": ["9" * 5000 + ":1"]},
    ):
        client.post(f"{path}/confirm-row-exclusions", data=invalid)
        assert not latest_row_exclusions(current.id)
    save_row_exclusions(original.id, [old_rows[0].id], "clear", "Restored.", planner_user.id,
                        original.capture_identity, max(entry.id for entry in previous.values()))
    client.post(f"{path}/confirm-row-exclusions", data=payload)
    assert not latest_row_exclusions(current.id)
    revision = max(entry.id for entry in latest_row_exclusions(original.id).values())
    save_row_exclusions(original.id, [old_rows[0].id], "exclude", "Checked original.", planner_user.id,
                        original.capture_identity, revision)
    previous = latest_row_exclusions(original.id)
    payload["suggestion"] = [f"{new_rows[i].id}:{previous[old_rows[i].id].id}" for i in range(2)]
    response = client.post(f"{path}/confirm-row-exclusions", data=payload, follow_redirects=True)
    assert b"Confirmed 2 suggested row exclusion" in response.data
    confirmed = latest_row_exclusions(current.id)
    assert {entry.reused_from_id for entry in confirmed.values()} == {entry.id for entry in previous.values()}
    assert {entry.rationale for entry in confirmed.values()} == {"Checked original."}
    assert not row_exclusion_suggestions(current, new_rows, confirmed)
    assert "0 matching saved records" in client.get(
        path + "?scope=exclusion_suggestions",
    ).get_data(as_text=True)
    revision = max(entry.id for entry in confirmed.values())
    adjusted = create_adjusted_extract(current.id, current.capture_identity, 0, planner_user.id,
                                       expected_row_revision=revision)
    assert adjusted.row_count == 1
    assert set(adjusted.source_settings["row_exclusion_ids"]) == {entry.id for entry in confirmed.values()}
    assert "confirmed from exclusion" in client.get(f"{PAGE}/extracts/{adjusted.id}").get_data(as_text=True)
    save_row_exclusions(current.id, [new_rows[0].id], "clear", "Restored.", planner_user.id,
                        current.capture_identity, revision)
    future = save_va05_extract(planner_user.id)
    future_rows = ForecastDataRecord.query.filter_by(extract_id=future.id).all()
    suggestions = row_exclusion_suggestions(future, future_rows, {})
    assert {record.data["order_num"] for record in future_rows if record.id in suggestions} == {13}
    assert "confirmed from exclusion" in client.get(path + "?scope=all").get_data(as_text=True)


def test_row_exclusion_suggestions_require_unique_unchanged_rows_and_matching_context(db_session, planner_user):
    live = add_source(db_session, order_num=12)
    add_source(db_session, order_num=13)
    original = save_va05_extract(planner_user.id)
    old_rows = ForecastDataRecord.query.filter_by(extract_id=original.id).order_by(ForecastDataRecord.id).all()
    save_row_exclusions(original.id, [row.id for row in old_rows], "exclude",
                        "Evidence.", planner_user.id, original.capture_identity, 0)
    live.release_qty = Decimal("4")
    add_source(db_session, order_num=13)
    current = save_va05_extract(planner_user.id)
    rows = ForecastDataRecord.query.filter_by(extract_id=current.id).all()
    assert row_exclusion_suggestions(current, rows, {}) == {}
    live.release_qty = Decimal("3")
    SalesOrder.query.filter_by(order_num=13).order_by(SalesOrder.id.desc()).first().release_qty = Decimal("4")
    db_session.commit()
    matching = save_va05_extract(planner_user.id)
    rows = ForecastDataRecord.query.filter_by(extract_id=matching.id).all()
    assert {row.data["order_num"] for row in rows if row.id in row_exclusion_suggestions(matching, rows, {})} == {12}
    matching.source_settings = {**matching.source_settings, "capture_environment": "different"}
    assert row_exclusion_suggestions(matching, rows, {}) == {}


def test_row_exclusion_suggestions_do_not_guess_between_identical_prior_rows(db_session, planner_user):
    duplicate = add_source(db_session)
    add_source(db_session)
    original = save_va05_extract(planner_user.id)
    rows = ForecastDataRecord.query.filter_by(extract_id=original.id).order_by(ForecastDataRecord.id).all()
    save_row_exclusions(original.id, [rows[0].id], "exclude", "Evidence.", planner_user.id,
                        original.capture_identity, 0)
    db_session.delete(duplicate)
    db_session.commit()
    current = save_va05_extract(planner_user.id)
    current_row = ForecastDataRecord.query.filter_by(extract_id=current.id).one()
    assert row_exclusion_suggestions(current, [current_row], {}) == {}


def test_adjustment_permissions_and_confirmation(db_session, planner_user, viewer_user, client):
    add_source(db_session)
    source = save_va05_extract(planner_user.id)
    row = ForecastDataRecord.query.filter_by(extract_id=source.id).one()
    payload = {"action": "exclude", "rationale": "Evidence", "expected_id": "0",
               "capture_identity": source.capture_identity}
    login(client, viewer_user.email, "Viewer!Pass1234")
    assert client.post(f"{PAGE}/records/{row.id}/adjustment", data=payload).status_code == 403
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(f"{PAGE}/records/{row.id}/adjustment", data=payload)
    assert response.status_code == 302
    entry = latest_adjustments(source.id)[(12, 1, 1)]
    response = client.post(f"{PAGE}/extracts/{source.id}/adjusted", data={
        "capture_identity": source.capture_identity, "expected_revision": entry.id,
    }, follow_redirects=True)
    assert b"Confirm the adjustments" in response.data
    assert ForecastDataExtract.query.count() == 1


def test_adjustment_form_shows_captured_row_and_prefills_active_evidence(
    db_session, planner_user, client,
):
    add_source(db_session, release_qty=Decimal("0"), release_price_gbp=None,
               req_date=None, void_line=False)
    add_source(db_session, job_num="J2", release_qty=Decimal("8"), void_line=True)
    source = save_va05_extract(planner_user.id)
    row = ForecastDataRecord.query.filter_by(extract_id=source.id).order_by(ForecastDataRecord.id).first()
    login(client, planner_user.email, "Planner!Pass1234")

    def form():
        page = client.get(f"{PAGE}/extracts/{source.id}").get_data(as_text=True)
        row_html = page.split(f'<tbody id="record-{row.id}"', 1)[1].split("</tbody>", 1)[0]
        return row_html.split('data-adjustment-form>', 1)[1].split("</form>", 1)[0]

    original = form()
    assert f'Captured on this row: 0.000</div>' in original
    assert 'Captured on this row: Missing</div>' in original
    assert 'Captured on this row: Not void</div>' in original
    assert 'name="release_qty" step="0.001" value=""' in original
    assert 'other rows in the release may differ' in original
    assert '<option value="correct" selected>' not in original

    adjustment = save_adjustment(
        row.id, "correct",
        {"release_qty": "5", "void_line": "false", "order_date": "2026-01-05",
         "order_date_scope": "order"},
        "Verified <invoice>.", planner_user.id, source.capture_identity, 0,
    )
    existing = form()
    assert '<option value="correct" selected>' in existing
    assert 'name="release_qty" step="0.001" value="5"' in existing
    assert 'name="release_price_gbp" step="0.0001" value=""' in existing
    assert '<option value="false" selected>Not void</option>' in existing
    assert 'name="order_date" value="2026-01-05"' in existing
    assert '<option value="order" selected>' in existing
    assert 'required>Verified &lt;invoice&gt;.</textarea>' in existing
    assert f'name="expected_id" value="{adjustment.id}"' in existing

    response = client.post(f"{PAGE}/records/{row.id}/adjustment", data={
        "capture_identity": source.capture_identity, "expected_id": adjustment.id,
        "action": "correct", "release_qty": "6", "void_line": "false",
        "order_date": "2026-01-05", "order_date_scope": "order", "rationale": "Updated evidence.",
    })
    assert response.status_code == 302
    current = latest_adjustments(source.id)[(12, 1, 1)]
    assert current.corrections == {
        "release_qty": "6", "void_line": False, "order_date": "2026-01-05",
        "order_date_scope": "order",
    }

    cleared = save_adjustment(row.id, "clear", {}, "No longer needed.",
                              planner_user.id, source.capture_identity, current.id)
    empty = form()
    assert f'name="expected_id" value="{cleared.id}"' in empty
    assert 'name="release_qty" step="0.001" value=""' in empty
    assert '<option value="correct" selected>' not in empty
    assert 'required></textarea>' in empty


def test_adjustment_migration_preserves_referenced_extracts():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "e81ad2397b4c_add_forecast_release_adjustments.py"
    migration = runpy.run_path(str(path))
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE forecast_data_extracts (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE records (id INTEGER PRIMARY KEY, extract_id INTEGER REFERENCES forecast_data_extracts(id) ON DELETE CASCADE)"))
        connection.execute(sa.text("INSERT INTO forecast_data_extracts VALUES (1)"))
        connection.execute(sa.text("INSERT INTO records VALUES (1, 1)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            assert connection.execute(sa.text("SELECT extract_id FROM records")).scalar_one() == 1
            assert connection.execute(sa.text("PRAGMA foreign_key_check")).all() == []
            migration["downgrade"]()
            assert connection.execute(sa.text("SELECT extract_id FROM records")).scalar_one() == 1
            assert connection.execute(sa.text("PRAGMA foreign_key_check")).all() == []


def test_adjustment_migration_compiles_for_postgresql():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "e81ad2397b4c_add_forecast_release_adjustments.py"
    migration = runpy.run_path(str(path))
    output = StringIO()
    context = MigrationContext.configure(
        dialect=postgresql.dialect(), opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration["upgrade"]()
        migration["downgrade"]()
    sql = output.getvalue()
    assert "FOREIGN KEY(derived_from_id) REFERENCES forecast_data_extracts (id) ON DELETE RESTRICT" in sql
    assert "CREATE TABLE forecast_release_adjustments" in sql


def test_row_exclusion_migration_preserves_existing_records():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "f41a7b3c9d20_add_forecast_row_exclusions.py"
    migration = runpy.run_path(str(path))
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE forecast_data_extracts (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE forecast_data_records (id INTEGER PRIMARY KEY, extract_id INTEGER REFERENCES forecast_data_extracts(id))"))
        connection.execute(sa.text("INSERT INTO forecast_data_extracts VALUES (1)"))
        connection.execute(sa.text("INSERT INTO forecast_data_records VALUES (1, 1)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            assert connection.execute(sa.text("SELECT extract_id FROM forecast_data_records")).scalar_one() == 1
            assert connection.execute(sa.text("PRAGMA foreign_key_check")).all() == []
            migration["downgrade"]()
            assert connection.execute(sa.text("SELECT extract_id FROM forecast_data_records")).scalar_one() == 1


def test_row_exclusion_suggestion_link_migration_preserves_evidence():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "b2869ef07c41_link_row_exclusion_suggestions.py"
    migration = runpy.run_path(str(path))
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE forecast_row_exclusions (id INTEGER PRIMARY KEY, rationale TEXT NOT NULL)"))
        connection.execute(sa.text("INSERT INTO forecast_row_exclusions VALUES (1, 'Evidence')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            connection.execute(sa.text("INSERT INTO forecast_row_exclusions (id, rationale, reused_from_id) VALUES (2, 'Evidence', 1)"))
            assert connection.execute(sa.text("PRAGMA foreign_key_check")).all() == []
            assert connection.execute(sa.text("SELECT rationale FROM forecast_row_exclusions WHERE id=1")).scalar_one() == "Evidence"
            migration["downgrade"]()
            assert connection.execute(sa.text("SELECT count(*) FROM forecast_row_exclusions")).scalar_one() == 2
    output = StringIO()
    context = MigrationContext.configure(dialect=postgresql.dialect(), opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration["upgrade"]()
        migration["downgrade"]()
    assert "FOREIGN KEY(reused_from_id) REFERENCES forecast_row_exclusions (id) ON DELETE RESTRICT" in output.getvalue()


def test_weekly_intake_matches_release_grain_and_flags_gaps():
    row = {
        "order_num": 12, "order_line": 1, "rel_num": 1, "assembly_seq": 0,
        "order_date": "2026-01-05", "release_price_gbp": "100", "void_line": False,
    }
    profile = explore_weekly_intake(
        [
            row, {**row, "job_num": "other-job"},
            {**row, "assembly_seq": 1, "release_price_gbp": "500"},
            {**row, "rel_num": 2, "release_price_gbp": "40", "void_line": True},
            {**row, "rel_num": 3, "void_line": None},
            {**row, "rel_num": 4}, {**row, "rel_num": 4, "release_price_gbp": "99"},
            {**row, "rel_num": 5, "order_date": "2026-01-19", "release_price_gbp": "0"},
            {**row, "rel_num": 6, "order_date": "2026-01-25"},
            {**row, "rel_num": 7, "order_date": "2025-12-29"},
        ],
        date(2026, 1, 21),
        detail_week=1,
    )
    first, gap, partial = profile.weeks
    assert (first.net_gbp, first.void_gbp) == (Decimal("100"), Decimal("40"))
    assert (first.releases, first.included, first.voided, first.exceptions) == (4, 1, 1, 2)
    assert gap.net_gbp is None and not gap.partial
    assert partial.net_gbp == 0 and partial.partial
    assert profile.outside_calendar == 1
    assert profile.issues == {
        "Releases with missing or conflicting GBP": 1,
        "Releases with missing or conflicting void flags": 1,
        "Releases dated after the capture": 1,
    }
    assert [
        (detail.key, detail.status, detail.gbp) for detail in profile.release_details
    ] == [
        ((12, 1, 1), "included", Decimal("100")),
        ((12, 1, 2), "voided", Decimal("40")),
        ((12, 1, 3), "excluded", Decimal("100")),
        ((12, 1, 4), "excluded", None),
    ]
    assert profile.release_details[2].reasons == (
        "Releases with missing or conflicting void flags",
    )


def test_weekly_review_exports_and_benchmarks_without_bridging_gaps(tmp_path):
    database = tmp_path / "capture.db"
    output = tmp_path / "review.csv"
    row = {
        "order_num": 12, "order_line": 1, "rel_num": 1, "assembly_seq": 0,
        "order_date": "2026-01-05", "release_price_gbp": "100", "void_line": False,
    }
    with closing(sqlite3.connect(database)) as conn:
        conn.executescript("""
            CREATE TABLE forecast_data_extracts (
                id, captured_at, rules_version, content_hash, row_count, source_settings, source
            );
            CREATE TABLE forecast_data_records (extract_id, data);
        """)
        conn.execute(
            "INSERT INTO forecast_data_extracts VALUES (1, ?, 'test', 'hash', 6, ?, 'bskyVA05v1')",
            ("2026-02-05T00:00:00Z", json.dumps({
                "fields": list(row), "capture_environment": "testing",
            })),
        )
        conn.executemany("INSERT INTO forecast_data_records VALUES (1, ?)", [
            (json.dumps(row),),
            (json.dumps({**row, "rel_num": 2, "order_date": "2026-01-12",
                         "release_price_gbp": "80"}),),
            (json.dumps({**row, "rel_num": 3, "order_date": "2026-01-12",
                         "void_line": None}),),
            (json.dumps({**row, "rel_num": 4, "order_date": "2026-01-26",
                         "release_price_gbp": "50"}),),
            (json.dumps({**row, "rel_num": 5, "order_date": "2026-01-26",
                         "release_price_gbp": "10", "void_line": True}),),
            (json.dumps({**row, "rel_num": 6, "order_date": "2026-02-02"}),),
        ])
        conn.commit()
    weeks = review(database, 1, output)
    with output.open(newline="", encoding="utf-8") as file:
        saved = list(csv.DictReader(file))
    assert [row["net_gbp"] for row in saved] == ["100.0000", "80.0000", "", "50.0000"]
    assert saved[1]["coverage"] == "incomplete_excluded_release"
    assert saved[2]["coverage"] == "unavailable"
    assert saved[3]["void_gbp"] == "10.0000"
    assert saved[0]["extract"] == "TEST-1"
    result = previous_week_benchmark(weeks, skip_exceptions=False)
    assert result is not None
    assert (result.pairs, result.mae, result.wape, result.bias) == (1, 20, 25, 20)
    assert previous_week_benchmark(weeks, skip_exceptions=True) is None
    assert review(database, 1, output) == weeks
    output.write_text("different\n", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        review(database, 1, output)
    assert output.read_text(encoding="utf-8") == "different\n"


def test_forecast_comparison_uses_prior_weeks_on_a_shared_window():
    weeks = [
        IntakeWeek(
            number=i,
            start=date(2026, 1, 5) + timedelta(weeks=i - 1),
            partial=i == 15,
            possible_shutdown=False,
            net_gbp=Decimal(i * 10) if i != 7 else None,
            exceptions=int(i == 3),
        )
        for i in range(1, 16) if i != 12
    ]
    assert compare_forecasts(weeks, lookback=4, skip_exceptions=False) == [
        ForecastComparison(5, Decimal(50), Decimal(40), Decimal(25)),
        ForecastComparison(6, Decimal(60), Decimal(50), Decimal(35)),
    ]
    assert compare_forecasts(weeks, lookback=4, skip_exceptions=True) == []
    with pytest.raises(ValueError, match="at least two"):
        compare_forecasts(weeks, lookback=1, skip_exceptions=False)


def test_weekly_review_verifies_multi_year_history_with_gap_and_partial_weeks(tmp_path):
    database = tmp_path / "capture.db"
    output = tmp_path / "history.csv"
    row = {
        "order_num": 12, "order_line": 1, "rel_num": 1, "assembly_seq": 0,
        "order_date": "2025-12-22", "release_price_gbp": "100", "void_line": False,
    }
    with closing(sqlite3.connect(database)) as conn:
        conn.executescript("""
            CREATE TABLE forecast_data_extracts (
                id, captured_at, rules_version, content_hash, row_count, source_settings, source
            );
            CREATE TABLE forecast_data_records (extract_id, data);
        """)
        conn.execute(
            "INSERT INTO forecast_data_extracts VALUES (1, ?, 'test', 'hash', 3, ?, 'bskyVA05v1')",
            ("2026-01-21T00:00:00Z", json.dumps({
                "fields": list(row), "capture_environment": "testing",
                "order_date_from": "2025-12-22", "order_date_to": "2026-01-21",
            })),
        )
        conn.executemany("INSERT INTO forecast_data_records VALUES (1, ?)", [
            (json.dumps(row),),
            (json.dumps({**row, "rel_num": 2, "order_date": "2026-01-05"}),),
            (json.dumps({**row, "rel_num": 3, "order_date": "2026-01-19"}),),
        ])
        conn.commit()
    weeks = review(database, 1, output, history=True)
    assert [week.start.isoformat() for week in weeks] == [
        "2025-12-22", "2025-12-29", "2026-01-05", "2026-01-12",
    ]
    assert [week.net_gbp for week in weeks] == [100, None, 100, None]
    with output.open(newline="", encoding="utf-8") as file:
        saved = list(csv.DictReader(file))
    assert [row["calendar"] for row in saved] == ["SOI 2025", "SOI 2025", "SOI 2026", "SOI 2026"]
    assert [row["financial_week"] for row in saved] == ["51", "52", "1", "2"]
    assert review(database, 1, output, history=True) == weeks


def test_lag_regression_uses_only_previous_training_pairs_and_common_targets():
    weeks = [
        IntakeWeek(
            number=i, start=date(2026, 1, 5) + timedelta(weeks=i - 1),
            partial=False, possible_shutdown=False, net_gbp=Decimal(i * 10),
            exceptions=int(i == 12),
        )
        for i in range(1, 23)
    ]
    result = compare_lag_regression(weeks, skip_exceptions=False)
    first = result.comparisons[0]
    assert first.week == 10 and first.training_pairs == 8
    assert (first.actual, first.previous_week, first.rolling_mean, first.regression) == (
        Decimal(100), Decimal(90), Decimal(75), Decimal(100),
    )
    assert result.insufficient_training == 5

    changed = weeks[:]
    changed[9] = replace(changed[9], net_gbp=Decimal(9999))
    changed[10] = replace(changed[10], net_gbp=Decimal(8888))
    changed_first = compare_lag_regression(changed, skip_exceptions=False).comparisons[0]
    assert changed_first.regression == first.regression
    assert changed_first.actual == 9999

    clean = compare_lag_regression(weeks, skip_exceptions=True)
    assert not {12, 13, 14, 15, 16} & {row.week for row in clean.comparisons}
    assert next(row for row in clean.comparisons if row.week == 17).training_pairs == 13
    no_first = compare_lag_regression(weeks, skip_exceptions=False, excluded_weeks=frozenset({1}))
    assert no_first.comparisons[0].week == 11
    assert next(row for row in no_first.comparisons if row.week == 17).training_pairs == 14

    spike = weeks[:]
    spike[0] = replace(spike[0], net_gbp=Decimal(1000))
    with_spike = compare_lag_regression(spike, skip_exceptions=False)
    without_spike = compare_lag_regression(spike, skip_exceptions=False, excluded_weeks=frozenset({1}))
    assert next(row for row in with_spike.comparisons if row.week == 17).regression != (
        next(row for row in without_spike.comparisons if row.week == 17).regression
    )


def test_lag_regression_never_bridges_missing_or_partial_weeks_and_reports_singular_fit():
    weeks = [
        IntakeWeek(
            number=i, start=date(2026, 1, 5) + timedelta(weeks=i - 1),
            partial=i == 17, possible_shutdown=False,
            net_gbp=None if i == 12 else Decimal(10),
        )
        for i in range(1, 23)
    ]
    result = compare_lag_regression(weeks, skip_exceptions=False)
    assert result.comparisons == []
    assert result.constant_predictor > 0
    varying = [
        replace(week, net_gbp=Decimal(week.number * 10) if week.net_gbp is not None else None)
        for week in weeks
    ]
    result = compare_lag_regression(varying, skip_exceptions=False)
    assert [row.week for row in result.comparisons] == [10, 11, 22]
    assert result.comparisons[-1].training_pairs == 16
    with pytest.raises(ValueError, match="chronological"):
        compare_lag_regression(list(reversed(varying)), skip_exceptions=False)


def test_lag_regression_fits_ordinary_least_squares():
    values = (1, 2, 1, 3, 2, 5, 4, 8, 7, 9)
    weeks = [
        IntakeWeek(
            number=i, start=date(2026, 1, 5) + timedelta(weeks=i - 1),
            partial=False, possible_shutdown=False, net_gbp=Decimal(value),
        )
        for i, value in enumerate(values, 1)
    ]
    x, y = values[:8], values[1:9]
    n = len(x)
    slope = Fraction(n * sum(a * b for a, b in zip(x, y)) - sum(x) * sum(y),
                     n * sum(a * a for a in x) - sum(x) ** 2)
    expected = Fraction(sum(y), n) + slope * (values[8] - Fraction(sum(x), n))
    result = compare_lag_regression(weeks, skip_exceptions=False)
    assert len(result.comparisons) == 1
    assert float(result.comparisons[0].regression) == pytest.approx(float(expected))


def test_lag_experiment_reports_common_weeks_and_unavailable_scores(capsys):
    weeks = [
        IntakeWeek(
            number=i, start=date(2026, 1, 5) + timedelta(weeks=i - 1),
            partial=False, possible_shutdown=False, net_gbp=Decimal(10),
        )
        for i in range(1, 12)
    ]
    report_lag_experiment(weeks)
    output = capsys.readouterr().out
    assert "IQR outliers identified retrospectively for sensitivity: none" in output
    assert "2 with constant training predictors" in output
    assert "Sensitivity comparison on target weeks common to all scenarios: none" in output
    assert "One-lag regression: no forecasts available." in output


def test_weekly_eda_keeps_missing_and_flagged_weeks_out_of_statistics():
    weeks = [
        IntakeWeek(
            number=i, start=date(2026, 1, 5) + timedelta(weeks=i - 1),
            partial=i == 8, possible_shutdown=i == 7,
            net_gbp=Decimal(value) if value is not None else None,
            exceptions=int(i == 3),
        )
        for i, value in enumerate((0, 10, 999, None, 40, 50, 60, 999), start=1)
    ]
    lines = describe_weekly_intake(weeks)
    assert lines[:3] == [
        "EDA coverage: 7 completed weeks; 5 unflagged observations",
        "Unavailable weeks: 4; exception weeks: 3; possible shutdown weeks: 7",
        "Net GBP: mean 32.00; median 40.00; range 0.00 to 60.00; zero weeks 1",
    ]
    assert "IQR outlier candidates: none" in lines
    assert any(
        line.startswith("  Without data-quality flagged weeks: 5 weeks; mean 32.00")
        for line in lines
    )
    assert "Lag 1 Pearson correlation: +1.00 (3 pairs)" in lines
    assert "Lag 2 Pearson correlation: unavailable (1 pairs)" in lines
    assert "Lag 4 Pearson correlation: unavailable (2 pairs)" in lines


def test_weekly_distribution_separates_outliers_flags_and_exclusions():
    values = {1: 1000, 2: 10, 3: 20, 4: 30, 5: 40, 6: None, 7: 500, 34: 100}
    weeks = [
        IntakeWeek(
            number=number, start=date(2026, 1, 5) + timedelta(weeks=number - 1),
            partial=number == 7, possible_shutdown=False,
            net_gbp=Decimal(value) if value is not None else None,
            exceptions=int(number == 34),
        )
        for number, value in values.items()
    ]

    distribution = summarize_weekly_distribution(weeks)

    assert distribution.completed_weeks == 7
    assert distribution.missing_weeks == (6,)
    assert distribution.partial_weeks == (7,)
    assert [week.number for week in distribution.quality_flagged_weeks] == [34]
    assert distribution.outlier_weeks == (1,)
    assert [
        (comparison.statistics.count, comparison.statistics.mean, comparison.statistics.median)
        for comparison in distribution.comparisons
    ] == [
        (6, Decimal(200), Decimal(35)),
        (5, Decimal(40), Decimal(30)),
        (5, Decimal(220), Decimal(30)),
        (4, Decimal(25), Decimal(25)),
    ]


def test_time_patterns_preserve_gaps_and_compare_matching_financial_weeks():
    def week(year, number, value, *, partial=False, exceptions=0):
        return IntakeWeek(
            number=number, start=financial_year_start(year) + timedelta(weeks=number - 1),
            partial=partial, possible_shutdown=False,
            net_gbp=Decimal(value) if value is not None else None, exceptions=exceptions,
        )

    weeks = [
        week(2024, 1, 10), week(2024, 2, 20), week(2024, 3, 30),
        week(2024, 4, None), week(2024, 5, 50), week(2024, 52, 70), week(2024, 53, 80),
        week(2025, 1, 100), week(2025, 2, 200), week(2025, 3, 300, exceptions=1),
        week(2025, 4, 400, partial=True),
    ]
    lags, _, comparisons = summarize_time_patterns(weeks)

    assert lags[0].pairs == 5
    assert [(row.target.start, row.previous_year.start) for row in comparisons] == [
        (date(2025, 1, 6), date(2024, 1, 1)),
        (date(2025, 1, 13), date(2024, 1, 8)),
    ]
    assert not any(row.target.number == 53 for row in comparisons)
    previous = score_forecasts([(row.previous_week.net_gbp, row.target.net_gbp) for row in comparisons])
    seasonal = score_forecasts([(row.previous_year.net_gbp, row.target.net_gbp) for row in comparisons])
    assert (previous.mae, previous.wape, previous.bias) == (Decimal(60), Decimal(40), Decimal(-60))
    assert (seasonal.mae, seasonal.wape, seasonal.bias) == (Decimal(135), Decimal(90), Decimal(-135))


def test_time_pattern_lags_report_outlier_sensitivity_and_constant_values():
    weeks = [
        IntakeWeek(number=index, start=date(2026, 1, 5) + timedelta(weeks=index - 1),
                   partial=False, possible_shutdown=False, net_gbp=Decimal(value))
        for index, value in enumerate([10, 10, 10, 1000, 10, 10, 10], start=1)
    ]
    lags, without_outliers, _ = summarize_time_patterns(weeks)
    assert lags[0].pairs == 6 and lags[0].correlation is not None
    assert without_outliers[0].pairs == 4 and without_outliers[0].correlation is None


def test_time_patterns_page_shows_history_and_common_benchmark(client, planner_user, db_session):
    extract = ForecastDataExtract(
        captured_at=datetime(2026, 1, 21, tzinfo=timezone.utc),
        captured_by_id=planner_user.id, source="bskyVA05v1", rules_version="test",
        row_count=4, content_hash="test",
        source_settings={"capture_environment": "testing", "fields": list(REQUIRED_FIELDS)},
        summary={},
    )
    db_session.add(extract)
    db_session.flush()
    for index, (order_date, amount) in enumerate([
        ("2025-01-06", "10"), ("2025-01-13", "20"),
        ("2025-12-29", "30"), ("2026-01-05", "40"),
    ], start=1):
        db_session.add(ForecastDataRecord(
            extract_id=extract.id, source_row_id=index,
            data={"order_num": index, "order_line": 1, "rel_num": 1, "assembly_seq": 0,
                  "order_date": order_date, "release_price_gbp": amount, "void_line": False},
            findings=[], has_findings=False,
        ))
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")

    response = client.get(f"{PAGE}/extracts/{extract.id}/time-patterns")

    assert response.status_code == 200
    html = " ".join(response.get_data(as_text=True).split())
    assert "SOI 2025" in html and "SOI 2026" in html
    assert "Unavailable (not zero)" in html
    assert "Same financial week last year" in html
    assert "Inspect 1 common target weeks" in html
    assert "not what was known at each historical cutoff" in html
    assert f"/extracts/{extract.id}/weeks/1?year=2026" in html
    assert ForecastDataRecord.query.filter_by(extract_id=extract.id).count() == 4
    assert ForecastDataNote.query.filter_by(extract_id=extract.id).count() == 0


def test_distribution_page_renders_read_only_saved_extract_comparisons(
    client, planner_user, db_session,
):
    extract = ForecastDataExtract(
        captured_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
        captured_by_id=planner_user.id,
        source="bskyVA05v1",
        rules_version="test",
        row_count=8,
        content_hash="test",
        source_settings={
            "capture_environment": "testing",
            "fields": list(REQUIRED_FIELDS),
        },
        summary={},
    )
    db_session.add(extract)
    db_session.flush()
    source_rows = [
        (1, "2026-01-05", "1000", False, 1),
        (2, "2026-01-12", "10", False, 1),
        (3, "2026-01-19", "20", False, 1),
        (4, "2026-01-26", "30", False, 1),
        (5, "2026-02-02", "40", False, 1),
        (34, "2026-08-24", "101", False, 1),
        (34, "2026-08-24", "99", False, 1),
        (34, "2026-08-24", "100", False, 2),
    ]
    db_session.add_all([
        ForecastDataRecord(
            extract_id=extract.id,
            source_row_id=index,
            data={
                "order_num": order_num,
                "order_line": 1,
                "rel_num": rel_num,
                "assembly_seq": 0,
                "order_date": order_date,
                "release_price_gbp": amount,
                "void_line": void_line,
            },
            findings=[],
            has_findings=False,
        )
        for index, (order_num, order_date, amount, void_line, rel_num) in enumerate(
            source_rows, start=1,
        )
    ])
    db_session.commit()
    record_count = ForecastDataRecord.query.filter_by(extract_id=extract.id).count()
    login(client, planner_user.email, "Planner!Pass1234")

    response = client.get(
        f"/ai-tools/data-readiness/extracts/{extract.id}/distribution",
    )

    assert response.status_code == 200
    html = " ".join(response.get_data(as_text=True).split())
    assert "Unusual weeks" in html
    assert 'data-fmt="uk-datetime">19/11/2026 00:00:00</time>' in html
    workspace_html = client.get(f"/ai-tools/eda/{extract.id}").get_data(as_text=True)
    assert 'data-fmt="uk-datetime">19/11/2026 00:00:00</time>' in workspace_html
    assert "week 1 by the 1.5x IQR rule" in html
    assert "Week 34: 1 exception" in html
    assert "200.00" in html
    assert "Without outlier or data-quality flagged weeks" in html
    assert "Partial weeks are excluded; missing weeks are never replaced with zero." in html
    assert ForecastDataRecord.query.filter_by(extract_id=extract.id).count() == record_count
    assert f"/extracts/{extract.id}/weeks/1" in html
    assert f"/extracts/{extract.id}/weeks/34" in html
    outlier = client.get(f"{PAGE}/extracts/{extract.id}/weeks/1")
    assert outlier.status_code == 200
    assert "Unusual weekly value, not a confirmed data error" in outlier.get_data(as_text=True)
    gap = client.get(f"{PAGE}/extracts/{extract.id}/weeks/6")
    assert gap.status_code == 200
    assert "Unavailable (not zero)" in gap.get_data(as_text=True)
    assert "No saved releases match this filter" in gap.get_data(as_text=True)


def test_intake_history_reviews_fixed_period_across_years_and_keeps_gaps(
    db_session, client, planner_user,
):
    add_source(db_session, rel_num=1, order_date=date(2024, 12, 30), void_line=False)
    add_source(db_session, rel_num=2, order_date=date(2025, 1, 5), void_line=False)
    add_source(db_session, rel_num=3, order_date=date(2025, 1, 13), void_line=False)
    for voided in (False, True):
        add_source(db_session, rel_num=4, order_date=date(2025, 1, 13), void_line=voided)
    add_source(db_session, rel_num=5, order_date=date(2025, 1, 20), void_line=False)
    extract = save_va05_extract(planner_user.id, period=(date(2024, 12, 30), date(2025, 1, 21)))
    extract.captured_at = datetime(2025, 1, 22, tzinfo=timezone.utc)
    db_session.commit()
    path = f"{PAGE}/extracts/{extract.id}/history"
    assert client.get(path).status_code == 302
    login(client, planner_user.email, "Planner!Pass1234")

    response = client.get(path)
    assert response.status_code == 200
    html = " ".join(response.get_data(as_text=True).split())
    assert "SOI 2024" in html and "SOI 2025" in html
    assert "Month 12</strong>" in html
    assert "/weeks/53?year=2024" in html
    assert "240.00" in html
    assert "30/12/2024 to 21/01/2025" in html
    assert "2 of 3 completed weeks with GBP</span> &middot; 1 unavailable &middot; 1 with release exceptions" in html
    assert "20/01/2025 to 26/01/2025" in html
    assert "23/12/2024" not in html and "27/01/2025" not in html
    assert "candidate training period" not in html
    base = f"{PAGE}/extracts/{extract.id}"
    week = client.get(f"{base}/weeks/53?year=2024").get_data(as_text=True)
    assert "SOI 2024 week 53" in week and "Order 12 / line 1 / release 2" in week
    partial = client.get(f"{base}/weeks/3?year=2025").get_data(as_text=True)
    assert "Partial week:" in partial and "Order 12 / line 1 / release 5" in partial
    assert client.get(f"{base}/weeks/52?year=2024").status_code == 404


def test_scoped_capture_keeps_full_releases_and_unassigned_dates(db_session, planner_user):
    start, end = date(2026, 1, 7), date(2026, 1, 18)
    main = add_source(db_session, order_date=start, void_line=False)
    assembly = add_source(db_session, order_date=date(2025, 1, 2), assembly_seq=1)
    last = add_source(db_session, rel_num=2, order_date=end, void_line=False)
    add_source(db_session, rel_num=3, order_date=end + timedelta(days=1), order_received_date=start)
    conflict_in = add_source(db_session, rel_num=4, order_date=start)
    conflict_out = add_source(db_session, rel_num=4, order_date=end + timedelta(days=1))
    missing = add_source(db_session, rel_num=5, order_date=None, order_received_date=start)
    extract = save_va05_extract(planner_user.id, period=(start, end))
    assert extract.order_period == (start, end)
    records = {record.source_row_id: record for record in ForecastDataRecord.query.filter_by(extract_id=extract.id)}
    assert records.keys() == {main.id, assembly.id, last.id, conflict_in.id, conflict_out.id, missing.id}
    assert extract.summary["unassigned_releases"] == 2
    assert {key for key, record in records.items() if "unassigned_order_date" in record.findings} == {
        conflict_in.id, conflict_out.id, missing.id,
    }
    profile = explore_weekly_intake(
        [record.data for record in records.values()], date(2026, 2, 1), period=extract.order_period,
    )
    assert [(week.number, week.partial, week.net_gbp) for week in profile.weeks] == [
        (1, True, Decimal("120")), (2, False, Decimal("120")),
    ]
    assert len(summarize_weekly_distribution(profile.weeks).chart_weeks) == 1
    other = save_va05_extract(planner_user.id, period=(start + timedelta(days=1), end))
    assert other.content_hash != extract.content_hash


def test_week_investigation_traces_saved_releases_and_preserves_flags_after_review(
    db_session, client, planner_user, viewer_user,
):
    source = {
        "order_date": date(2026, 1, 5), "void_line": False, "model": "Sofa",
        "size_desc": "Large", "prod_size": "3-seat", "customer_group": "Trade",
    }
    original = add_source(db_session, **source)
    add_source(db_session, **{**source, "release_price_gbp": Decimal("99"), "void_line": True})
    for _ in range(2):
        add_source(db_session, **{**source, "rel_num": 2, "release_price_gbp": Decimal("300")})
    add_source(db_session, **{
        **source, "rel_num": 2, "assembly_seq": 1, "order_date": date(2026, 1, 12),
        "release_price_gbp": Decimal("900"),
    })
    add_source(db_session, **{
        **source, "rel_num": 3, "void_line": True, "release_price_gbp": Decimal("50"),
    })
    add_source(db_session, **{
        **source, "rel_num": 4, "order_date": date(2026, 1, 12),
        "release_price_gbp": Decimal("800"),
    })
    extract = save_va05_extract(planner_user.id)
    extract.captured_at = datetime(2026, 1, 14, tzinfo=timezone.utc)
    original.release_price_gbp = Decimal("9999")
    db_session.commit()
    before = [
        (record.id, record.data, record.findings)
        for record in ForecastDataRecord.query.filter_by(extract_id=extract.id).order_by(
            ForecastDataRecord.id,
        )
    ]
    path = f"{PAGE}/extracts/{extract.id}/weeks/1"
    assert client.get(path).status_code == 302
    login(client, viewer_user.email, "Viewer!Pass1234")
    html = " ".join(client.get(path).get_data(as_text=True).split())
    assert "05/01/2026 to 11/01/2026" in html
    assert "<td>05/01/2026</td>" in html
    assert "Incomplete: 1 release excluded" in html
    assert "Releases with missing or conflicting GBP" in html
    assert "Releases with missing or conflicting void flags" in html
    assert "No review evidence" in html
    assert "Add a note (no customer or personal details)" not in html
    assert client.post(f"{PAGE}/extracts/{extract.id}/notes", data={
        "week": "1", "body": "Evidence", "capture_identity": extract.capture_identity,
    }).status_code == 403
    for suffix in ("?scope=invalid", "?page=bad", "?page=0"):
        assert client.get(path + suffix).status_code == 400
    for number in (0, 2, 53):
        assert client.get(f"{PAGE}/extracts/{extract.id}/weeks/{number}").status_code == 404
    all_html = client.get(path + "?scope=all").get_data(as_text=True)
    assert 'class="card ai-release-list"' in all_html
    assert all_html.index('id="week-summary"') < all_html.index('id="week-contributors"') < all_html.index('id="week-releases"')
    assert '<details open>\n      <summary class="ai-release-summary">' in all_html
    assert '<details>\n      <summary class="ai-release-summary">' in all_html
    assert "300.00" in all_html and "50.00 GBP excluded as void" in all_html
    assert "Trade: 300.00 GBP" in all_html
    assert "Subassembly: not counted" in all_html
    assert "9999" not in all_html and "800.00" not in all_html
    redirected = client.get(path + "?scope=included&page=999")
    assert redirected.status_code == 302
    assert "scope=included" in redirected.location and "page=1" in redirected.location
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    record = ForecastDataRecord.query.filter_by(
        extract_id=extract.id, source_row_id=original.id,
    ).one()
    release_review = client.get(
        f"{PAGE}/extracts/{extract.id}?scope=all&release={record.id}&week=1",
    )
    review_html = release_review.get_data(as_text=True)
    assert "Back to week 1 investigation" in review_html
    assert 'name="week" value="1"' in review_html
    assert '<th scope="col">Void</th>' in review_html
    assert "<strong>Received:</strong> 02/01/2026" in review_html
    assert '<td data-field="order_date">05/01/2026</td>' in review_html
    assert '<td data-field="req_date">01/02/2026</td>' in review_html
    assert record.data["order_date"] == "2026-01-05"
    review_response = client.post(f"{PAGE}/records/{record.id}/review", data={
        "status": "accepted", "rationale": "Reviewed both observations; reconciliation still required.",
        "capture_identity": extract.capture_identity,
        "scope": "all", "release_context": "1", "week": "1",
    })
    assert review_response.status_code == 302
    assert "week=1" in review_response.location
    assert f"release={record.id}" in review_response.location
    reviewed_html = client.get(path).get_data(as_text=True)
    assert "Accepted with evidence" in reviewed_html
    assert "Incomplete: 1 release excluded" in reviewed_html
    assert "reconciliation still required" in reviewed_html
    note_response = client.post(f"{PAGE}/extracts/{extract.id}/notes", data={
        "week": "1", "body": "Explained business event: closure delayed order entry.",
        "capture_identity": extract.capture_identity,
    })
    assert note_response.location.endswith("/weeks/1#extract-notes")
    note = ForecastDataNote.query.filter_by(extract_id=extract.id).one()
    assert note.body == "SOI 2026 week 1: Explained business event: closure delayed order entry."
    assert note.body in client.get(path).get_data(as_text=True)
    client.post(f"{PAGE}/extracts/{extract.id}/notes", data={
        "week": "1", "body": " ", "capture_identity": extract.capture_identity,
    })
    assert ForecastDataNote.query.filter_by(extract_id=extract.id).count() == 1
    assert client.post(f"{PAGE}/extracts/{extract.id}/notes", data={
        "week": "bad", "body": "Evidence", "capture_identity": extract.capture_identity,
    }).status_code == 400
    assert before == [
        (record.id, record.data, record.findings)
        for record in ForecastDataRecord.query.filter_by(extract_id=extract.id).order_by(
            ForecastDataRecord.id,
        )
    ]


def test_week_investigation_paginates_distinct_releases_in_value_order(
    db_session, client, planner_user,
):
    for number in range(1, 27):
        add_source(
            db_session, rel_num=number, order_date=date(2026, 1, 5),
            void_line=False, release_price_gbp=Decimal(number),
        )
    extract = save_va05_extract(planner_user.id)
    extract.captured_at = datetime(2026, 1, 12, tzinfo=timezone.utc)
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")
    path = f"{PAGE}/extracts/{extract.id}/weeks/1?scope=included"
    first = client.get(path).get_data(as_text=True)
    second = client.get(path + "&page=2").get_data(as_text=True)
    assert "Page 1 of 2" in first and "Page 2 of 2" in second
    assert "Order 12 / line 1 / release 26" in first
    assert "Order 12 / line 1 / release 1</strong>" not in first
    assert "Order 12 / line 1 / release 1</strong>" in second
    assert "26 matching releases" in second
    assert "351.00" in first and "351.00" in second


def test_release_mix_reconciles_without_assigning_conflicting_labels(
    db_session, planner_user,
):
    source = {
        "order_date": date(2026, 1, 5), "void_line": False, "model": "Sofa",
        "size_desc": "Large", "prod_size": "3-seat", "customer_group": "Trade",
    }
    add_source(db_session, **source)
    add_source(db_session, **{**source, "job_num": "J2", "size_desc": "Small"})
    add_source(db_session, **{**source, "assembly_seq": 1, "model": "Scatter"})
    add_source(db_session, **{**source, "rel_num": 2, "customer_group": None})
    add_source(db_session, **{**source, "rel_num": 3, "void_line": True})
    extract = save_va05_extract(planner_user.id)
    axes = ("Product model", "Size within model", "Epicor customer group")
    profile = explore_weekly_intake(
        (row.data for row in ForecastDataRecord.query.filter_by(extract_id=extract.id)),
        date(2026, 1, 12), mix_axes=axes,
    )
    assert profile.weeks[0].net_gbp == 240
    assert profile.mix["Product model"][1][("assigned", ("Sofa",))].releases == 2
    assert profile.mix["Size within model"][1][("conflicting", ())].gbp == 120
    assert profile.mix["Epicor customer group"][1][("missing", ())].releases == 1
    lines = describe_mix(profile)
    assert "Production size within model: unavailable (not captured)" in lines
    assert "  conflicting: 1 releases, 120.00 GBP" in lines
    assert "  missing: 1 releases, 120.00 GBP" in lines
    assert {"model", "size_desc", "prod_size", "customer_group"}.issubset(
        extract.source_settings["fields"],
    )


def test_weekly_intake_page_reads_saved_capture_and_handles_legacy_extract(
    db_session, client, planner_user, viewer_user,
):
    source = add_source(db_session, order_date=date(2026, 12, 21), void_line=False)
    extract = save_va05_extract(planner_user.id)
    legacy = save_va05_extract(planner_user.id)
    for saved in (extract, legacy):
        saved.captured_at = datetime(2026, 12, 22, tzinfo=timezone.utc)
    legacy.source_settings = {
        **legacy.source_settings,
        "fields": [field for field in legacy.source_settings["fields"] if field != "void_line"],
    }
    source.release_price_gbp = Decimal("999")
    source.void_line = True
    db_session.commit()
    path = f"{PAGE}/extracts/{extract.id}/weekly-intake"
    assert client.get(path).status_code == 302
    login(client, viewer_user.email, "Viewer!Pass1234")
    response = client.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "SOI 2026" in html
    assert "21/12/2026 to 27/12/2026" in html
    assert "Outlined bars: partial or flagged" in html
    assert "120.00 GBP" in html
    assert "999.00" not in html
    assert "Partial week" in html and "Possible shutdown" in html
    assert "No usable completed weeks" in html
    assert "No usable observations" in html
    review = client.get(f"{PAGE}/extracts/{extract.id}").get_data(as_text=True)
    assert f'href="/ai-tools/eda/{extract.id}"' in review
    assert f'href="{PAGE}/extracts/{extract.id}/history"' in review
    unavailable = client.get(f"{PAGE}/extracts/{legacy.id}/weekly-intake")
    assert unavailable.status_code == 200
    assert "including the void flag" in unavailable.get_data(as_text=True)
    assert "120.00 GBP" not in unavailable.get_data(as_text=True)
    assert client.get(f"{PAGE}/extracts/{legacy.id}/weeks/1").status_code == 400


def test_weekly_intake_mix_uses_capture_metadata_and_excludes_partial_week(
    db_session, client, planner_user, viewer_user,
):
    source = {
        "order_date": date(2026, 1, 5), "void_line": False, "model": "Sofa",
        "size_desc": "Large", "prod_size": "3-seat", "customer_group": "Trade",
    }
    add_source(db_session, **source)
    add_source(db_session, **{**source, "rel_num": 2, "customer_group": None})
    add_source(db_session, **{
        **source, "rel_num": 3, "order_date": date(2026, 1, 12), "model": "Partial",
    })
    current = save_va05_extract(planner_user.id)
    legacy = save_va05_extract(planner_user.id)
    for saved in (current, legacy):
        saved.captured_at = datetime(2026, 1, 13, tzinfo=timezone.utc)
    legacy.source_settings = {
        **legacy.source_settings,
        "fields": [
            field for field in legacy.source_settings["fields"]
            if field not in {"size_desc", "prod_size", "customer_group"}
        ],
    }
    db_session.commit()
    assert client.get("/ai-tools/eda").status_code == 302
    login(client, viewer_user.email, "Viewer!Pass1234")

    hub = client.get(f"/ai-tools/eda/{current.id}").get_data(as_text=True)
    assert current.reference in hub and "4 of 4 dimensions captured" in hub
    assert f'{PAGE}/extracts/{current.id}/history' in hub
    assert f'{PAGE}/extracts/{current.id}/weekly-intake"' in hub
    assert f'{PAGE}/extracts/{current.id}/weekly-intake#mix-heading' in hub
    plan = client.get(
        f"/ai-tools/workflow/feature-decisions?extract_id={current.id}", follow_redirects=True,
    ).get_data(as_text=True)
    assert "Validate availability at each forecast cutoff" in plan
    assert f'href="/ai-tools/eda/{current.id}"' in plan
    latest = client.get("/ai-tools/eda").get_data(as_text=True)
    assert legacy.reference in latest and "1 of 4 dimensions captured" in latest

    def mix_html(extract_id):
        response = client.get(f"{PAGE}/extracts/{extract_id}/weekly-intake")
        assert response.status_code == 200
        return response.get_data(as_text=True).split('id="mix-heading"', 1)[1].split("</section>", 1)[0]

    html = mix_html(current.id)
    assert "Product and customer mix" in html
    assert html.count(">1</td>") == 4
    assert html.count("1 of 1") == 4
    assert "50.0%" in html and "1 release<br>" in html and "120.00 GBP" in html
    assert "Not captured" not in html
    old_html = mix_html(legacy.id)
    assert old_html.count("Not captured") == 3
    assert "100.0%" in old_html and "50.0%" not in old_html


def test_saved_rows_show_captured_customer_and_product_details_without_live_backfill(
    db_session, client, planner_user,
):
    source = add_source(
        db_session, customer_id="C123", customer_group="Trade", model="Sofa",
        size_desc="Large", prod_size="3-seat",
    )
    current = save_va05_extract(planner_user.id)
    legacy = save_va05_extract(planner_user.id)
    old_row = ForecastDataRecord.query.filter_by(extract_id=legacy.id).one()
    old_row.data = {key: value for key, value in old_row.data.items() if key != "customer_id"}
    legacy.source_settings = {
        **legacy.source_settings,
        "fields": [field for field in legacy.source_settings["fields"] if field != "customer_id"],
    }
    source.customer_id = "LIVE-CHANGED"
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")

    html = client.get(f"{PAGE}/extracts/{current.id}?scope=all").get_data(as_text=True)
    assert 'class="table table-sm ai-review-table"' in html
    table = html.split('<table class="table table-sm ai-review-table">', 1)[1].split("</table>", 1)[0]
    record = ForecastDataRecord.query.filter_by(extract_id=current.id).one()
    assert table.count("<thead>") == 1
    assert f'aria-controls="record-detail-{record.id}" aria-expanded="false"' in table
    assert f'<details id="record-detail-{record.id}" data-row-details>' in table
    assert '<td data-field="release_price_gbp">120.0000</td>' in html
    assert '<td data-field="release_qty">3.000</td>' in html
    assert "GBP / quantity" not in html
    for value in ("C123", "Trade", "Sofa", "Large", "3-seat"):
        assert value in html
    assert "LIVE-CHANGED" not in html
    old_html = client.get(f"{PAGE}/extracts/{legacy.id}?scope=all").get_data(as_text=True)
    assert '<td data-field="customer_id">Not captured</td>' in old_html
    assert "LIVE-CHANGED" not in old_html
    source.customer_id = None
    db_session.commit()
    missing = save_va05_extract(planner_user.id)
    missing_html = client.get(f"{PAGE}/extracts/{missing.id}?scope=all").get_data(as_text=True)
    assert '<td data-field="customer_id">Missing</td>' in missing_html


def test_expanded_review_shows_all_findings_and_context(
    db_session, client, planner_user,
):
    add_source(db_session, release_qty=None, release_price_gbp=Decimal("0"))
    extract = save_va05_extract(planner_user.id)
    record = ForecastDataRecord.query.filter_by(extract_id=extract.id).one()
    login(client, planner_user.email, "Planner!Pass1234")

    for selected, other in (
        ("missing_qty", "nonpositive_value"),
        ("nonpositive_value", "missing_qty"),
    ):
        html = client.get(
            f"{PAGE}/extracts/{extract.id}",
            query_string={"finding": selected},
        ).get_data(as_text=True)
        row = html.split(f'id="record-{record.id}" data-review-row>', 1)[1]
        summary, expanded = row.split('<tr data-review-detail-row>', 1)
        assert extract.summary["rules"][selected] in summary
        assert extract.summary["rules"][selected] in expanded
        assert extract.summary["rules"][other] in expanded
        assert '<h4 class="h6">Record context</h4>' in expanded
        assert '<h4 class="h6">Review evidence</h4>' in expanded
        assert "Planning adjustment for this release" in expanded
        assert "<strong>Received:</strong>" in expanded
        assert '<h4 class="h6">Findings' in expanded
        assert "Received:" in expanded and "Current model category:" in expanded
        assert f"Source row {record.source_row_id}" in expanded
        for label in ("Order date:", "Release qty:", "Customer ID:", "Void line:"):
            assert label not in expanded


def test_extract_is_reproducible_and_survives_operational_refresh(
    db_session,
    planner_user,
):
    source = add_source(
        db_session,
        release_qty=None,
        release_price_gbp=Decimal("0"),
        customer_id="C123",
        customer_name="Do not copy",
        sur_name="Do not copy",
        order_book_comments="Do not copy",
    )
    extract = save_va05_extract(planner_user.id)
    again = save_va05_extract(planner_user.id)
    assert "company_scope" not in extract.source_settings
    assert extract.content_hash == again.content_hash
    record = ForecastDataRecord.query.filter_by(extract_id=extract.id).one()
    assert record.data["release_qty"] is None
    assert Decimal(record.data["release_price_gbp"]) == 0
    assert record.data["customer_id"] == "C123"
    assert "customer_id" in extract.source_settings["fields"]
    assert "customer_name" not in record.data
    assert "sur_name" not in record.data
    assert "order_book_comments" not in record.data
    assert set(record.findings) == {"missing_qty", "nonpositive_value"}
    assert extract.summary["counts"] == {
        "missing_qty": 1,
        "nonpositive_value": 1,
    }
    # Verify the documented hash against the saved observations, not live rows.
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {
                "source": extract.source,
                "rules_version": extract.rules_version,
                "source_settings": extract.source_settings,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(b"\n")
    digest.update(
        json.dumps(
            record.data,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert digest.hexdigest() == extract.content_hash

    source.release_qty = Decimal("5")
    db_session.commit()
    changed = save_va05_extract(planner_user.id)
    assert changed.content_hash != extract.content_hash
    SalesOrder.query.delete()
    db_session.commit()
    db_session.expire_all()
    assert (
        db_session.get(ForecastDataRecord, record.id).data["release_qty"]
        is None
    )
    assert db_session.get(ForecastDataExtract, extract.id).row_count == 1
    assert (
        AuditLog.query.filter_by(
            action="forecast_data_extract_saved",
        ).count()
        == 3
    )


def test_main_row_grain_and_conflicts_are_checked_without_summing(
    db_session,
    planner_user,
):
    add_source(db_session)
    add_source(
        db_session,
        assembly_seq=1,
        release_qty=Decimal("0"),
        release_price_gbp=Decimal("0"),
    )
    add_source(
        db_session,
        job_num="J2",
        release_qty=Decimal("4"),
        release_price_gbp=Decimal("140"),
    )
    add_source(
        db_session,
        order_line=2,
        assembly_seq=1,
        release_qty=Decimal("0"),
        release_price_gbp=None,
        order_received_date=date(2026, 1, 3),
    )
    extract = save_va05_extract(planner_user.id)
    rows = ForecastDataRecord.query.order_by(ForecastDataRecord.id).all()
    assert extract.row_count == 4
    assert extract.summary["release_count"] == 2
    assert extract.summary["order_count"] == 1
    for row in rows[:3]:
        assert set(row.findings) == {
            "conflicting_qty",
            "conflicting_value",
            "conflicting_received",
        }
    assert set(rows[3].findings) == {"missing_main", "conflicting_received"}
    assert "nonpositive_qty" not in extract.summary["counts"]
    assert SalesOrder.query.count() == 4


def test_extract_reference_uses_saved_environment_not_current_config(
    app, db_session, client, planner_user, monkeypatch,
):
    add_source(db_session)
    monkeypatch.setitem(app.config, "APP_ENVIRONMENT", "development")
    dev = save_va05_extract(planner_user.id)
    monkeypatch.setitem(app.config, "APP_ENVIRONMENT", "production")
    live = save_va05_extract(planner_user.id)
    legacy = save_va05_extract(planner_user.id)
    legacy.source_settings = {
        key: value for key, value in legacy.source_settings.items()
        if key != "capture_environment"
    }
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")

    html = client.get(PAGE).get_data(as_text=True)
    for extract, prefix in (
        (dev, "DEV"), (live, "LIVE"), (legacy, "UNLABELLED"),
    ):
        reference = f"{prefix}-{extract.id}"
        assert f"Extract {reference}" in html
        review = client.get(f"{PAGE}/extracts/{extract.id}")
        assert f"<strong>{reference}</strong>" in review.get_data(as_text=True)


def test_consistent_main_repeats_are_not_quantity_conflicts(
    db_session,
    planner_user,
):
    add_source(db_session)
    add_source(db_session, assembly_seq=None, job_num="J2")
    extract = save_va05_extract(planner_user.id)
    assert extract.summary["counts"] == {}


def test_release_assemblies_are_visible_without_entering_review_queue(
    db_session, client, planner_user,
):
    source = add_source(db_session, model="SOFA")
    for assembly in (4, 5, 6):
        add_source(
            db_session, assembly_seq=assembly, model=f"SUB-{assembly}",
            release_qty=Decimal("0"), release_price_gbp=Decimal("0"),
        )
    add_source(db_session, order_num=13, model="OTHER-ORDER", release_qty=None)
    add_source(db_session, order_line=2, model="OTHER-LINE")
    add_source(db_session, rel_num=2, model="OTHER-RELEASE")
    extract = save_va05_extract(planner_user.id)
    assert extract.summary["counts"] == {"missing_qty": 1}
    assert extract.summary["flagged_rows"] == 1
    assert "repeated_release" not in extract.summary["rules"]
    login(client, planner_user.email, "Planner!Pass1234")
    path = f"{PAGE}/extracts/{extract.id}"
    html = client.get(path).get_data(as_text=True)
    assert "Order 12 / line 1" not in html
    assert "Order 13 / line 1" in html

    record = ForecastDataRecord.query.filter_by(
        extract_id=extract.id, source_row_id=source.id,
    ).one()
    html = client.get(f"{path}?scope=all").get_data(as_text=True)
    assert "View all rows for this release" in html
    assert f"release={record.id}" in html
    SalesOrder.query.delete()
    db_session.commit()
    response = client.get(path, query_string={
        "scope": "all", "release": record.id,
    })
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "4 matching saved records" in html
    for assembly in (0, 4, 5, 6):
        assert f'<td data-field="assembly_seq">{assembly}</td>' in html
    for model in ("SOFA", "SUB-6"):
        assert f'<td data-field="model">{model}</td>' in html
    assert "OTHER-" not in html
    assert "Save review evidence" not in html


def test_release_context_and_legacy_findings_stay_in_their_extract(
    db_session, client, planner_user,
):
    add_source(db_session)
    add_source(db_session, assembly_seq=4)
    old = save_va05_extract(planner_user.id)
    old.rules_version = "va05-local-v1"
    label = "Release appears on multiple stored rows (review grain)"
    old.summary = {
        **old.summary, "flagged_rows": 2,
        "rules": {"repeated_release": label},
        "counts": {"repeated_release": 2},
    }
    for record in ForecastDataRecord.query.filter_by(extract_id=old.id):
        record.findings = ["repeated_release"]
        record.has_findings = True
    db_session.commit()
    new = save_va05_extract(planner_user.id)
    old_record = ForecastDataRecord.query.filter_by(
        extract_id=old.id, source_row_id=1,
    ).one()
    login(client, planner_user.email, "Planner!Pass1234")

    response = client.get(
        f"{PAGE}/extracts/{old.id}",
        query_string={"finding": "repeated_release"},
    )
    assert response.status_code == 200
    assert "2 matching saved records" in response.get_data(as_text=True)
    assert client.get(
        f"{PAGE}/extracts/{new.id}",
        query_string={"scope": "all", "release": old_record.id},
    ).status_code == 404
    assert client.get(
        f"{PAGE}/extracts/{new.id}", query_string={"release": "invalid"},
    ).status_code == 400
    response = client.post(
        f"{PAGE}/records/{old_record.id}/review",
        data={
            "status": "accepted", "rationale": "Expected assembly detail.",
            "scope": "all", "release_context": "1",
            "capture_identity": old.capture_identity,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert f"release={old_record.id}" in response.request.url
    assert "Expected assembly detail." in response.get_data(as_text=True)


def test_row_exceptions_preserve_missing_and_nonpositive_values(
    db_session,
    planner_user,
):
    add_source(
        db_session,
        order_received_date=None,
        req_date=None,
        release_qty=Decimal("-1"),
        release_price_gbp=None,
        ium=" ",
    )
    add_source(
        db_session,
        order_num=13,
        req_date=date(2026, 1, 1),
        release_qty=None,
        release_price_gbp=Decimal("-1"),
    )
    extract = save_va05_extract(planner_user.id)
    assert extract.summary["counts"] == {
        "missing_received": 1,
        "missing_ship_by": 1,
        "nonpositive_qty": 1,
        "missing_value": 1,
        "missing_uom": 1,
        "ship_by_before_order": 1,
        "missing_qty": 1,
        "nonpositive_value": 1,
    }


def test_empty_source_reports_error_without_leaving_a_saved_extract(
    db_session,
    planner_user,
):
    with pytest.raises(ValidationError, match="No local VA05 rows"):
        save_va05_extract(planner_user.id)
    assert ForecastDataExtract.query.count() == 0
    assert ForecastDataRecord.query.count() == 0
    assert (
        AuditLog.query.filter_by(
            action="forecast_data_extract_saved",
        ).count()
        == 0
    )


def test_capture_preserves_every_row_across_write_batches(
    db_session,
    planner_user,
):
    db_session.execute(
        sa.insert(SalesOrder),
        [
            {
                "order_num": number,
                "order_line": 1,
                "rel_num": 1,
                "assembly_seq": 0,
                "release_qty": Decimal("1"),
            }
            for number in range(1, 504)
        ],
    )
    db_session.commit()
    extract = save_va05_extract(planner_user.id)
    assert extract.row_count == 503
    records = ForecastDataRecord.query.filter_by(extract_id=extract.id).all()
    assert len(records) == 503
    assert {row.data["order_num"] for row in records} == set(range(1, 504))
    assert extract.summary["counts"]["missing_received"] == 503


def test_review_history_appends_and_never_changes_findings(
    db_session,
    planner_user,
):
    add_source(db_session, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    record = ForecastDataRecord.query.one()
    save_data_decision(
        record.id,
        "investigate",
        "Need to check the release UOM.",
        planner_user.id,
    )
    save_data_decision(
        record.id,
        "accepted",
        "Reviewed the source report; see check 17.",
        planner_user.id,
    )
    db_session.expire_all()
    record = db_session.get(ForecastDataRecord, record.id)
    assert [item.status for item in record.decisions] == [
        "accepted",
        "investigate",
    ]
    assert record.findings == ["missing_qty"]
    assert record.data["release_qty"] is None
    assert extract.summary["flagged_rows"] == 1
    assert SalesOrder.query.one().release_qty is None
    assert (
        AuditLog.query.filter_by(
            action="forecast_data_decision_saved",
        ).count()
        == 2
    )


@pytest.mark.parametrize(
    ("status", "rationale"),
    [("invalid", "Evidence"), ("accepted", " "), ("accepted", "x" * 2001)],
)
def test_invalid_reviews_do_not_create_decisions(
    db_session,
    planner_user,
    status,
    rationale,
):
    add_source(db_session, release_qty=None)
    save_va05_extract(planner_user.id)
    with pytest.raises(ValidationError):
        save_data_decision(
            ForecastDataRecord.query.one().id,
            status,
            rationale,
            planner_user.id,
        )
    assert ForecastDataDecision.query.count() == 0


def test_reviews_require_a_saved_row_with_findings(db_session, planner_user):
    with pytest.raises(NotFoundError):
        save_data_decision(999, "accepted", "Reviewed.", planner_user.id)
    add_source(db_session)
    save_va05_extract(planner_user.id)
    with pytest.raises(ValidationError, match="no findings"):
        save_data_decision(
            ForecastDataRecord.query.one().id,
            "accepted",
            "Reviewed.",
            planner_user.id,
        )
    assert ForecastDataDecision.query.count() == 0


def test_legacy_extract_hides_company_scope_without_changing_provenance(
    db_session, client, planner_user,
):
    add_source(db_session)
    extract = save_va05_extract(planner_user.id)
    legacy_settings = dict(extract.source_settings)
    legacy_settings["company_scope"] = "Unverified: legacy scope"
    extract.source_settings = legacy_settings
    record = ForecastDataRecord.query.filter_by(extract_id=extract.id).one()
    digest = hashlib.sha256()
    digest.update(json.dumps({
        "source": extract.source,
        "rules_version": extract.rules_version,
        "source_settings": legacy_settings,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(b"\n")
    digest.update(json.dumps(
        record.data, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    old_hash = digest.hexdigest()
    extract.content_hash = old_hash
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")

    html = client.get(
        f"{PAGE}/extracts/{extract.id}?scope=all",
    ).get_data(as_text=True)
    assert "company_scope" not in html
    assert "Unverified: legacy scope" not in html
    db_session.expire_all()
    saved = db_session.get(ForecastDataExtract, extract.id)
    assert saved.source_settings == legacy_settings
    assert saved.content_hash == old_hash


def test_saved_model_shows_current_classification_without_changing_findings(
    db_session, client, planner_user,
):
    sofa = save_model_category("Sofas", "", planner_user.id)
    scatters = save_model_category("Scatters", "", planner_user.id)
    save_model_classification("SOFA", sofa.id, planner_user.id)
    save_model_classification("SCATTER", scatters.id, planner_user.id)
    save_model_classification_updates([("", scatters.id)], planner_user.id)
    add_source(db_session, order_num=1, model=" SOFA ", release_qty=None)
    add_source(db_session, order_num=2, model="SCATTER", release_qty=None)
    add_source(db_session, order_num=3, model="sofa", release_qty=None)
    add_source(db_session, order_num=4, model=None, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    first = ForecastDataRecord.query.filter_by(
        extract_id=extract.id, source_row_id=1,
    ).one()
    before_data = dict(first.data)
    before_findings = list(first.findings)
    before_hash = extract.content_hash
    assert first.data["model"] == " SOFA "
    assert "model" in extract.source_settings["fields"]
    login(client, planner_user.email, "Planner!Pass1234")

    html = client.get(
        f"{PAGE}/extracts/{extract.id}",
    ).get_data(as_text=True)
    for model in (" SOFA ", "SCATTER", "sofa", "Missing"):
        assert f'<td data-field="model">{model}</td>' in html
    assert "<strong>Current model category:</strong> Sofas" in html
    assert html.count("<strong>Current model category:</strong> Scatters") == 2
    assert "<strong>Current model category:</strong> Unclassified" in html
    assert "not historical assignments" in html

    save_model_classification("SOFA", scatters.id, planner_user.id)
    SalesOrder.query.delete()
    db_session.commit()
    html = client.get(
        f"{PAGE}/extracts/{extract.id}",
    ).get_data(as_text=True)
    assert html.count("<strong>Current model category:</strong> Scatters") == 3
    db_session.expire_all()
    assert db_session.get(ForecastDataRecord, first.id).data == before_data
    saved_record = db_session.get(ForecastDataRecord, first.id)
    assert saved_record.findings == before_findings
    assert (
        db_session.get(ForecastDataExtract, extract.id).content_hash
        == before_hash
    )


def test_old_extract_does_not_backfill_model_from_live_order(
    db_session, client, planner_user,
):
    source = add_source(db_session, model="CURRENT", release_qty=None)
    extract = save_va05_extract(planner_user.id)
    record = ForecastDataRecord.query.filter_by(extract_id=extract.id).one()
    record.data = {
        key: value for key, value in record.data.items() if key != "model"
    }
    db_session.commit()
    source.model = "LATER"
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")
    html = client.get(
        f"{PAGE}/extracts/{extract.id}",
    ).get_data(as_text=True)
    assert '<td data-field="model">Not captured</td>' in html
    assert "LATER" not in html
    db_session.expire_all()
    assert "model" not in db_session.get(ForecastDataRecord, record.id).data


def test_browser_capture_review_and_read_only_permissions(
    db_session,
    client,
    planner_user,
    viewer_user,
):
    add_source(db_session, release_qty=None)
    assert client.post(PAGE + "/capture").status_code == 302
    assert client.get(PAGE + "/extracts/1").status_code == 302
    login(client, planner_user.email, "Planner!Pass1234")
    assert "Enter both extract dates" in client.post(
        PAGE + "/capture", follow_redirects=True,
    ).get_data(as_text=True)
    assert "ordered date range" in client.post(PAGE + "/capture", data={
        "order_date_from": "04/01/2026", "order_date_to": "02/01/2026",
    }, follow_redirects=True).get_data(as_text=True)
    assert ForecastDataExtract.query.count() == 0
    response = client.post(PAGE + "/capture", data={
        "order_date_from": "01/01/2026", "order_date_to": "04/01/2026",
    }, follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Missing main-row release quantity" in html
    assert "Unverified: observed release values only" in html
    record = ForecastDataRecord.query.one()
    extract = ForecastDataExtract.query.one()
    review_url = f"{PAGE}/records/{record.id}/review"
    response = client.post(
        review_url,
        data={
            "status": "source_correction",
            "rationale": "Check the BAQ expression.",
            "capture_identity": extract.capture_identity,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Check the BAQ expression." in response.get_data(as_text=True)
    prefilled = client.get(f"{PAGE}?copy_from={extract.id}").get_data(as_text=True)
    assert 'value="01/01/2026"' in prefilled and 'value="04/01/2026"' in prefilled
    assert (
        client.get(
            f"{PAGE}/extracts/{extract.id}?scope=unreviewed",
        ).status_code
        == 200
    )

    client.get("/auth/logout")
    login(client, viewer_user.email, "Viewer!Pass1234")
    assert client.get(PAGE).status_code == 200
    html = client.get(
        f"{PAGE}/extracts/{extract.id}",
    ).get_data(as_text=True)
    assert "Check the BAQ expression." in html
    assert "textarea" not in html
    assert client.post(PAGE + "/capture").status_code == 403
    assert client.get(f"{PAGE}/extracts/{extract.id}/source-corrections.csv").status_code == 403
    assert (
        client.post(
            review_url,
            data={
                "status": "accepted",
                "rationale": "Not permitted.",
            },
        ).status_code
        == 403
    )
    assert ForecastDataExtract.query.count() == 1
    assert ForecastDataDecision.query.count() == 1


def test_source_correction_export_uses_only_latest_decisions(db_session, client, planner_user):
    add_source(db_session, release_qty=None)
    add_source(db_session, rel_num=2, release_qty=None)
    extract = save_va05_extract(planner_user.id, period=(date(2026, 1, 1), date(2026, 1, 4)))
    first, second = ForecastDataRecord.query.order_by(ForecastDataRecord.id).all()
    for record, status, reason in (
        (first, "source_correction", "Superseded correction"),
        (first, "accepted", "Validated in source"),
        (second, "source_correction", "=Check the source, then confirm."),
    ):
        save_data_decision(record.id, status, reason, planner_user.id, capture_identity=extract.capture_identity)
    path = f"{PAGE}/extracts/{extract.id}/source-corrections.csv"
    assert client.get(path).status_code == 302
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.get(path)
    assert response.status_code == 200 and response.mimetype == "text/csv"
    assert response.headers["Content-Disposition"] == f'attachment; filename="{extract.reference}-source-corrections.csv"'
    row, = csv.DictReader(StringIO(response.get_data(as_text=True)))
    assert (row["Extract"], row["Saved record ID"], row["Order"], row["Line"], row["Release"]) == (
        extract.reference, str(second.id), "12", "1", "2",
    )
    assert row["Reason"] == "'=Check the source, then confirm."
    assert ForecastDataDecision.query.count() == 3


def test_nonpositive_gbp_groups_reconcile_and_show_saved_release_evidence(
    db_session, client, planner_user, viewer_user,
):
    for line, amount, voided, assembly in (
        (1, "0", True, 0), (2, "0", False, 0), (3, "-25", False, 0),
        (4, "0", True, 0), (4, "927.90", False, 0),
        (5, "0", None, 0), (6, "0", False, 2), (2, "123", True, 2),
    ):
        add_source(
            db_session, order_line=line, assembly_seq=assembly,
            release_price_gbp=Decimal(amount), void_line=voided,
        )
    extract = save_va05_extract(planner_user.id)
    review_url = f"{PAGE}/extracts/{extract.id}"
    url = f"{review_url}/nonpositive-gbp"
    assert client.get(url).status_code == 302
    login(client, viewer_user.email, "Viewer!Pass1234")
    finding = client.get(f"{review_url}?finding=nonpositive_value").get_data(as_text=True)
    assert "Nonpositive GBP breakdown" in finding
    for key, label, count in (
        ("all", "All groups", 5),
        ("void_zero", "Zero GBP, consistently void", 1),
        ("nonvoid_zero", "Zero GBP, consistently not void", 1),
        ("uncertain", "Conflicting or incomplete main observations", 2),
        ("negative", "Negative GBP", 1),
    ):
        summary_row = finding.split(f'data-gbp-review-group="{key}"', 1)[1].split("</tr>", 1)[0]
        assert label in summary_row
        assert f'data-gbp-review-status="unreviewed">{count}</span>' in summary_row
    overview = " ".join(client.get(url).get_data(as_text=True).split())
    assert extract.summary["counts"]["nonpositive_value"] == 5
    for label, count in (
        ("Zero GBP, consistently void", 1), ("Zero GBP, consistently not void", 1),
        ("Conflicting or incomplete main observations", 2), ("Negative GBP", 1),
    ):
        assert f"{label}: {count}" in overview
    conflict = next(
        row for row in ForecastDataRecord.query.filter_by(extract_id=extract.id).all()
        if row.data["order_line"] == 4 and "nonpositive_value" in row.findings
    )
    grouped_url = f"{review_url}?scope=unreviewed&finding=nonpositive_value&gbp_group=uncertain"
    response = client.get(grouped_url)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "2 matching saved records" in html
    assert "Order 12 / line 4 / release 1" in html
    assert "Order 12 / line 5 / release 1" in html
    assert "Order 12 / line 1 / release 1" not in html
    collapsed = html.split(f'id="record-{conflict.id}" data-review-row>', 1)[1].split("</tr>", 1)[0]
    assert '<td data-field="order_date">02/01/2026</td>' in collapsed
    assert '<td data-field="req_date">01/02/2026</td>' in collapsed
    assert '<td data-field="void_line">Yes</td>' in collapsed
    assert '<td data-field="release_price_gbp">0' in collapsed
    assert f"release={conflict.id}&amp;scope=unreviewed#release-evidence" in html
    assert client.get(f"{review_url}?finding=nonpositive_value&gbp_group=unknown").status_code == 400
    for group, lines in (
        ("void_zero", (1,)), ("nonvoid_zero", (2,)), ("negative", (3,)), ("uncertain", (4, 5)),
    ):
        response = client.get(f"{url}?group={group}")
        assert response.status_code == 200
        group_table = response.get_data(as_text=True).split('<tbody>', 1)[1].split('</tbody>', 1)[0]
        assert tuple(line for line in range(1, 7) if f">12 / {line} / 1</a>" in group_table) == lines
    response = client.get(f"{url}?group=uncertain&release={conflict.id}")
    assert response.status_code == 200
    evidence = response.get_data(as_text=True).split('id="release-evidence"', 1)[1]
    assert "927.90" in evidence and "<td>Yes</td>" in evidence and "<td>No</td>" in evidence
    assert client.get(f"{url}?group=void_zero&release={conflict.id}").status_code == 404
    assert client.get(f"{url}?group=unknown").status_code == 400
    assert ForecastDataDecision.query.count() == 0
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    manager_view = client.get(grouped_url).get_data(as_text=True)
    assert "Set review outcome for 2 matching rows" in manager_view
    assert "Download a worklist by current status for this group" in manager_view
    for status in ("unreviewed", "investigate"):
        assert f"{review_url}/review-worklist/{status}.csv?finding=nonpositive_value&amp;gbp_group=uncertain" in manager_view
    response = client.post(f"{PAGE}/records/{conflict.id}/review", data={
        "capture_identity": extract.capture_identity, "scope": "unreviewed",
        "finding": "nonpositive_value", "gbp_group": "uncertain",
        "status": "investigate", "rationale": "Compare the main observations.",
    }, follow_redirects=True)
    assert response.status_code == 200 and "gbp_group=uncertain" in response.request.url
    assert "1 matching saved records" in response.get_data(as_text=True)


def test_gbp_group_bulk_review_and_export_stay_in_selected_group(db_session, client, planner_user):
    for order_num in range(1, 29):
        add_source(db_session, order_num=order_num, release_price_gbp=0, void_line=order_num < 28)
    extract = save_va05_extract(planner_user.id)
    url = f"{PAGE}/extracts/{extract.id}"
    bulk = f"{url}/finding-reviews"
    csv_url = f"{url}/source-corrections.csv?finding=nonpositive_value"
    login(client, planner_user.email, "Planner!Pass1234")
    payload = {
        "capture_identity": extract.capture_identity, "finding": "nonpositive_value",
        "gbp_group": "void_zero", "scope": "unreviewed", "expected_count": 27,
        "expected_revision": 0, "confirm": "yes", "status": "source_correction",
        "rationale": "Check the saved voids.",
    }
    response = client.post(bulk, data=payload, follow_redirects=True)
    assert "gbp_group=void_zero" in response.request.url
    assert "for 27 rows." in response.get_data(as_text=True)
    assert ForecastDataDecision.query.count() == 27

    def exported_orders(group=""):
        response = client.get(csv_url + (f"&gbp_group={group}" if group else ""))
        assert response.status_code == 200
        return {int(row["Order"]) for row in csv.DictReader(StringIO(response.get_data(as_text=True)))}

    assert exported_orders("void_zero") == set(range(1, 28))
    assert exported_orders("nonvoid_zero") == set()
    assert exported_orders() == set(range(1, 28))
    latest = ForecastDataDecision.query.order_by(ForecastDataDecision.id.desc()).first().id
    response = client.post(bulk, data={
        **payload, "scope": "findings", "expected_revision": latest,
        "status": "accepted", "rationale": "Verified the reason for the voids.",
    }, follow_redirects=True)
    assert "for 27 rows." in response.get_data(as_text=True)
    assert ForecastDataDecision.query.count() == 54
    assert not exported_orders()
    accepted_html = response.get_data(as_text=True)
    void_summary = accepted_html.split('data-gbp-review-group="void_zero"', 1)[1].split("</tr>", 1)[0]
    assert 'data-gbp-review-status="accepted">27</span>' in void_summary
    assert 'data-gbp-review-status="source_correction">0</span>' in void_summary
    assert "scope=accepted&amp;finding=nonpositive_value&amp;gbp_group=void_zero" in void_summary
    record = ForecastDataRecord.query.filter_by(extract_id=extract.id).order_by(ForecastDataRecord.id).first()
    response = client.post(f"{url}/reviews?finding=nonpositive_value", json={
        "capture_identity": extract.capture_identity,
        "reviews": [{"record_id": record.id, "status": "investigate", "rationale": "Check again."}],
    })
    assert response.status_code == 200
    assert response.json["gbp_counts"] == {
        "all": {"accepted": 26, "investigate": 1, "unreviewed": 1},
        "void_zero": {"accepted": 26, "investigate": 1},
        "nonvoid_zero": {"unreviewed": 1}, "negative": {}, "uncertain": {},
    }
    assert client.post(bulk, data={**payload, "gbp_group": "unknown"}).status_code == 400
    assert client.get(csv_url + "&gbp_group=unknown").status_code == 400


def test_review_worklists_export_flagged_rows_by_current_status(
    db_session, client, planner_user, viewer_user,
):
    for order_num, amount, voided in (
        (1, 0, True), (2, 0, True), (3, 0, False), (4, 0, True), (5, 120, False),
    ):
        add_source(db_session, order_num=order_num, release_price_gbp=amount, void_line=voided)
    extract = save_va05_extract(planner_user.id)
    records = {r.data["order_num"]: r for r in ForecastDataRecord.query.filter_by(extract_id=extract.id)}
    for order, outcome, reason in (
        (2, "investigate", "Superseded"), (2, "accepted", "Verified"),
        (3, "investigate", "Check value"), (4, "investigate", "Check void"),
    ):
        save_data_decision(
            records[order].id, outcome, reason, planner_user.id, capture_identity=extract.capture_identity,
        )
    url = f"{PAGE}/extracts/{extract.id}/review-worklist"
    login(client, viewer_user.email, "Viewer!Pass1234")
    assert client.get(f"{url}/unreviewed.csv").status_code == 403
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")

    def worklist(status, **filters):
        response = client.get(f"{url}/{status}.csv", query_string=filters)
        assert response.status_code == 200 and response.mimetype == "text/csv"
        return list(csv.DictReader(StringIO(response.get_data(as_text=True))))

    pending, = worklist("unreviewed")
    assert pending["Order"] == "1"
    assert pending["Decision"] == "Not reviewed"
    assert pending["Reason"] == pending["Reviewed at (ISO)"] == ""
    assert [row["Order"] for row in worklist("investigate")] == ["3", "4"]
    scoped, = worklist("investigate", finding="nonpositive_value", gbp_group="void_zero")
    assert (scoped["Order"], scoped["Decision"], scoped["Reason"]) == ("4", "Needs investigation", "Check void")
    assert not worklist("unreviewed", finding="nonpositive_value", gbp_group="nonvoid_zero")
    assert client.get(f"{url}/invalid.csv").status_code == 400


def test_ai_tools_workflow_header_tracks_stage_and_preserves_extract(
    db_session, client, planner_user,
):
    add_source(db_session, order_date=date(2026, 1, 5), release_qty=None)
    extract = save_va05_extract(planner_user.id)
    login(client, planner_user.email, "Planner!Pass1234")
    saved = f"{PAGE}/extracts/{extract.id}"
    eda = f"/ai-tools/eda/{extract.id}"
    for path, current, has_context in (
        ("/ai-tools/dashboard", "Overview", True),
        (PAGE, "Prepare data", False),
        ("/ai-tools/models", "Product categories", False),
        (saved, "Prepare data", True),
        (saved + "/history", "Prepare data", True),
        (eda, "Explore intake", True),
        (saved + "/weekly-intake", "Explore intake", True),
        (saved + "/distribution", "Explore intake", True),
        (saved + "/weeks/1", "Explore intake", True),
        (saved + "/time-patterns", "Explore intake", True),
        (saved + "/features", "Explore intake", True),
        (f"/ai-tools/workflow/experiments?extract_id={extract.id}", "Compare models", True),
        ("/ai-tools/workflow/forecasts", None, False),
        ("/ai-tools/workflow/monitoring", None, False),
    ):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        sidebar = html.split('id="aiToolsMenu">', 1)[1].split("</ul>", 1)[0]
        active = re.findall(
            r'<a class="nav-link active"\s+href="[^"]+">\s*<i[^>]*></i>([^<]+)',
            sidebar,
        )
        assert [label.strip() for label in active] == ([current] if current else [])
        assert html.count('aria-label="Breadcrumb"') == 1
        assert 'aria-current="step"' not in html
        mobile_menu = html.split('aria-label="AI Tools navigation">', 1)[1].split("</nav>", 1)[0]
        assert "Prepare data" in mobile_menu and "Compare models" in mobile_menu
        if has_context:
            assert 'aria-label="Selected data"' in html
            assert f"<strong>{extract.reference}</strong>" in html
            assert f'href="{saved}"' in sidebar and f'href="{eda}"' in sidebar
            assert f'extract_id={extract.id}' in sidebar
            assert f'href="{saved}"' in mobile_menu and f'href="{eda}"' in mobile_menu


def test_overview_recommends_evidence_follow_up_and_keeps_selected_extract(
    db_session, client, planner_user,
):
    add_source(db_session, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    newer = save_va05_extract(planner_user.id)
    record = ForecastDataRecord.query.filter_by(extract_id=extract.id).first()
    login(client, planner_user.email, "Planner!Pass1234")
    path = f"/ai-tools/dashboard?extract_id={extract.id}"
    html = client.get(path).get_data(as_text=True)
    assert "Review outstanding findings" in html
    assert f'href="{PAGE}/extracts/{extract.id}?scope=unreviewed#saved-records"' in html
    assert f"<strong>{newer.reference}</strong>" not in html

    save_data_decision(
        record.id, "source_correction", "Correct at source.", planner_user.id,
        capture_identity=extract.capture_identity,
    )
    html = client.get(path).get_data(as_text=True)
    assert "Follow up findings" in html
    assert "scope=source_correction#saved-records" in html

    save_data_decision(
        record.id, "accepted", "Evidence recorded.", planner_user.id,
        capture_identity=extract.capture_identity,
    )
    html = client.get(path).get_data(as_text=True)
    assert "Completed reviews do not certify the data" in html
    assert f'href="/ai-tools/eda/{extract.id}"' in html
    assert client.get("/ai-tools/dashboard?extract_id=999").status_code == 404
    assert client.get("/ai-tools/workflow/forecasts?extract_id=999").status_code == 404


def test_bulk_finding_outcome_across_pages_drives_correction_export(
    db_session, client, planner_user, viewer_user,
):
    ship_by = date(2026, 1, 1)
    for order_num in range(1, 82):
        add_source(db_session, order_num=order_num, req_date=ship_by)
    add_source(db_session, order_num=82, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    records = ForecastDataRecord.query.order_by(ForecastDataRecord.id).all()
    for record, status in (
        (records[0], "accepted"), (records[1], "investigate"), (records[-1], "source_correction"),
    ):
        save_data_decision(record.id, status, "Existing evidence.", planner_user.id, capture_identity=extract.capture_identity)
    url = f"{PAGE}/extracts/{extract.id}"
    finding = "ship_by_before_order"
    revision = records[1].decisions[0].id
    payload = {
        "capture_identity": extract.capture_identity, "finding": finding,
        "scope": "findings", "status": "source_correction", "expected_count": 81,
        "expected_revision": revision, "confirm": "yes",
        "rationale": "Verify the ship-by date in Epicor.",
    }
    bulk_url = f"{url}/finding-reviews"
    csv_url = f"{url}/source-corrections.csv?finding={finding}"
    login(client, viewer_user.email, "Viewer!Pass1234")
    assert client.post(bulk_url, data=payload).status_code == 403
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.get(url, query_string={"scope": "findings", "finding": finding})
    html = response.get_data(as_text=True)
    assert "81 matching saved records" in html
    assert "Set review outcome for 81 matching rows" in html
    assert "replaces the current outcome on 2 previously reviewed rows" in html
    assert f'id="record-{records[80].id}"' not in html
    unreviewed = client.get(url, query_string={"scope": "unreviewed", "finding": finding}).get_data(as_text=True)
    assert "Set review outcome for 79 matching rows" in unreviewed
    assert not list(csv.DictReader(StringIO(client.get(csv_url).get_data(as_text=True))))
    for invalid in (
        {**payload, "confirm": ""},
        {**payload, "expected_revision": revision - 1},
        {**payload, "expected_count": 79},
        {**payload, "capture_identity": "stale"},
        {**payload, "rationale": " "},
        {**payload, "status": "invalid"},
    ):
        assert client.post(bulk_url, data=invalid).status_code == 302
        assert ForecastDataDecision.query.count() == 3
    result = client.post(bulk_url, data=payload, follow_redirects=True)
    assert "for 81 rows. No Epicor data was changed." in result.get_data(as_text=True)
    assert ForecastDataDecision.query.filter_by(status="source_correction").count() == 82
    assert [decision.status for decision in records[0].decisions] == ["source_correction", "accepted"]
    assert [decision.status for decision in records[1].decisions] == ["source_correction", "investigate"]
    assert AuditLog.query.filter_by(action="forecast_data_decision_saved").count() == 84
    assert ForecastDataExtract.query.one().summary["counts"][finding] == 81

    response = client.get(csv_url)
    exported = list(csv.DictReader(StringIO(response.get_data(as_text=True))))
    assert {int(row["Order"]) for row in exported} == set(range(1, 82))
    assert {row["Order date (ISO)"] for row in exported} == {"2026-01-02"}
    assert {row["Required ship-by date (ISO)"] for row in exported} == {ship_by.isoformat()}
    assert {row["Reason"] for row in exported} == {payload["rationale"]}
    assert client.post(bulk_url, data=payload).status_code == 302
    assert ForecastDataDecision.query.count() == 84
    latest = max(record.decisions[0].id for record in records[:-1])
    assert client.post(bulk_url, data={
        **payload, "status": "accepted", "rationale": "Verified with source owner.",
        "expected_revision": latest,
    }).status_code == 302
    assert ForecastDataDecision.query.count() == 165
    assert not list(csv.DictReader(StringIO(client.get(csv_url).get_data(as_text=True))))


def test_batch_review_updates_distinct_rows_and_returns_latest_counts(
    db_session, client, planner_user, viewer_user,
):
    add_source(db_session, release_qty=None)
    add_source(db_session, rel_num=2, release_qty=None, release_price_gbp=None)
    extract = save_va05_extract(planner_user.id)
    records = ForecastDataRecord.query.order_by(ForecastDataRecord.id).all()
    save_data_decision(
        records[0].id, "investigate", "Check original order.", planner_user.id,
        capture_identity=extract.capture_identity,
    )
    before = [(record.data, record.findings) for record in records]
    url = f"{PAGE}/extracts/{extract.id}"
    payload = {"capture_identity": extract.capture_identity, "reviews": [
        {"record_id": records[0].id, "status": "accepted", "rationale": "Checked original order."},
        {"record_id": records[1].id, "status": "source_correction", "rationale": "Check <quantity>."},
    ]}
    login(client, viewer_user.email, "Viewer!Pass1234")
    assert client.post(f"{url}/reviews", json=payload).status_code == 403
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    html = client.get(url).get_data(as_text=True)
    assert f'data-save-url="{url}/reviews"' in html
    assert f'<tbody id="record-{records[0].id}" data-review-row>' in html

    response = client.post(f"{url}/reviews", json=payload)
    assert response.status_code == 200 and response.is_json
    assert response.json["counts"] == {"accepted": 1, "source_correction": 1}
    assert response.json["finding_counts"] == {"missing_qty": 2, "missing_value": 1}
    assert [entry["record_id"] for entry in response.json["reviews"]] == [record.id for record in records]
    assert response.json["reviews"][0]["badge"] == "text-bg-success"
    assert response.json["reviews"][1]["badge"] == "text-bg-danger"
    assert "Check &lt;quantity&gt;." in response.json["reviews"][1]["history_entry"]
    assert [(record.data, record.findings) for record in records] == before
    assert ForecastDataDecision.query.count() == 3
    assert AuditLog.query.filter_by(action="forecast_data_decision_saved").count() == 3
    html = client.get(url).get_data(as_text=True)
    assert '<span data-reviewed>2</span>' in html
    assert f'data-unreviewed href="{url}?scope=unreviewed&amp;finding=missing_qty#saved-records" hidden' in html
    assert '<span data-no-unreviewed>0</span>' in html
    finding_row = html.split('data-finding-progress="missing_qty"', 1)[1].split("</tr>", 1)[0]
    assert 'data-finding-state data-state="reviewed"' in finding_row
    assert 'data-finding-status>All affected rows reviewed.' in finding_row


def test_remembered_reviews_require_confirmation_and_keep_provenance(
    db_session, client, planner_user, viewer_user,
):
    add_source(db_session, order_num=12, release_qty=None)
    add_source(db_session, order_num=13, release_qty=None)
    original = save_va05_extract(planner_user.id)
    old_rows = ForecastDataRecord.query.filter_by(extract_id=original.id).order_by(ForecastDataRecord.id).all()
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(f"{PAGE}/extracts/{original.id}/reviews", json={
        "capture_identity": original.capture_identity,
        "reviews": [
            {"record_id": row.id, "status": "accepted", "rationale": "Checked original.",
             "remember": True}
            for row in old_rows
        ],
    })
    assert response.status_code == 200
    decisions = ForecastDataDecision.query.order_by(ForecastDataDecision.id).all()
    assert all(decision.remember for decision in decisions)
    newer = save_va05_extract(planner_user.id)
    new_rows = ForecastDataRecord.query.filter_by(extract_id=newer.id).order_by(ForecastDataRecord.id).all()
    url = f"{PAGE}/extracts/{newer.id}"
    html = client.get(url).get_data(as_text=True)
    assert "Previously reviewed: 2 suggestion(s)" in html
    assert "Checked original." in html
    assert f"{PAGE}/records/{old_rows[0].id}/evidence" in html
    assert f"release={old_rows[0].id}" in client.get(f"{PAGE}/records/{old_rows[0].id}/evidence").location
    assert ForecastDataDecision.query.count() == 2
    payload = {
        "capture_identity": newer.capture_identity, "confirm": "yes",
        "suggestion": [f"{row.id}:{decision.id}" for row, decision in zip(new_rows, decisions)],
    }
    client.get("/auth/logout")
    login(client, viewer_user.email, "Viewer!Pass1234")
    assert client.post(f"{url}/confirm-suggestions", data=payload).status_code == 403
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    assert client.post(f"{url}/confirm-suggestions", data=payload).status_code == 302
    copied = ForecastDataDecision.query.filter(ForecastDataDecision.record_id.in_([r.id for r in new_rows])).order_by(ForecastDataDecision.id).all()
    assert [(d.status, d.rationale, d.reused_from_id, d.remember) for d in copied] == [
        ("accepted", "Checked original.", decision.id, True) for decision in decisions
    ]
    assert "Based on" in client.get(f"{url}?scope=all").get_data(as_text=True)
    response = client.post(f"{url}/confirm-suggestions", data=payload, follow_redirects=True)
    assert "suggestion or review has changed" in response.get_data(as_text=True)
    assert ForecastDataDecision.query.count() == 4
    later = save_va05_extract(planner_user.id)
    assert "Previously reviewed: 2 suggestion(s)" in client.get(f"{PAGE}/extracts/{later.id}").get_data(as_text=True)


def test_suggestions_reject_changed_orders_and_stale_or_withdrawn_reviews(
    db_session, client, planner_user,
):
    source = add_source(db_session, release_qty=None)
    neighbour = add_source(db_session, rel_num=2)
    other = add_source(db_session, order_num=13, release_qty=None)
    original = save_va05_extract(planner_user.id)
    old_rows = ForecastDataRecord.query.filter_by(extract_id=original.id).order_by(ForecastDataRecord.id).all()
    first = save_data_decision(old_rows[0].id, "accepted", "Evidence.", planner_user.id, remember=True)
    second = save_data_decision(old_rows[2].id, "accepted", "Other evidence.", planner_user.id, remember=True)
    newer = save_va05_extract(planner_user.id)
    new_rows = ForecastDataRecord.query.filter_by(extract_id=newer.id).order_by(ForecastDataRecord.id).all()
    login(client, planner_user.email, "Planner!Pass1234")
    url = f"{PAGE}/extracts/{newer.id}"
    payload = {"capture_identity": newer.capture_identity, "confirm": "yes", "suggestion": [
        f"{new_rows[0].id}:{first.id}", f"{new_rows[2].id}:{second.id}",
    ]}
    save_data_decision(old_rows[2].id, "investigate", "Reopened.", planner_user.id)
    response = client.post(f"{url}/confirm-suggestions", data=payload, follow_redirects=True)
    assert "suggestion or review has changed" in response.get_data(as_text=True)
    assert ForecastDataDecision.query.filter(ForecastDataDecision.record_id.in_([r.id for r in new_rows])).count() == 0
    neighbour.release_qty = Decimal("4")
    other.release_price_gbp = Decimal("0")
    db_session.commit()
    changed = save_va05_extract(planner_user.id)
    html = client.get(f"{PAGE}/extracts/{changed.id}").get_data(as_text=True)
    assert "Previously reviewed" not in html
    assert source.release_qty is None


def test_duplicate_observations_are_not_suggested(db_session, client, planner_user):
    add_source(db_session, release_qty=None)
    add_source(db_session, release_qty=None)
    original = save_va05_extract(planner_user.id)
    row = ForecastDataRecord.query.filter_by(extract_id=original.id).first()
    save_data_decision(row.id, "accepted", "Evidence.", planner_user.id, remember=True)
    newer = save_va05_extract(planner_user.id)
    login(client, planner_user.email, "Planner!Pass1234")
    assert "Previously reviewed" not in client.get(f"{PAGE}/extracts/{newer.id}").get_data(as_text=True)


def test_review_status_filters_use_latest_outcome(db_session, client, planner_user):
    for order_num in range(1, 5):
        add_source(db_session, order_num=order_num, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    first, second, third, fourth = ForecastDataRecord.query.order_by(ForecastDataRecord.id).all()
    for record, status in (
        (first, "source_correction"), (first, "accepted"),
        (second, "source_correction"), (third, "investigate"),
    ):
        save_data_decision(record.id, status, "Reviewed.", planner_user.id, capture_identity=extract.capture_identity)
    login(client, planner_user.email, "Planner!Pass1234")
    url = f"{PAGE}/extracts/{extract.id}"
    for scope, record, badge, label in (
        ("accepted", first, "success", "Accepted with evidence"),
        ("source_correction", second, "danger", "Source correction required"),
        ("investigate", third, "warning", "Needs investigation"),
        ("unreviewed", fourth, "secondary", "Not reviewed"),
    ):
        response = client.get(url, query_string={"scope": scope, "finding": "missing_qty"})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert f'href="{url}?scope={scope}#saved-records"' in html
        assert f'id="record-{record.id}"' in html
        assert f'text-bg-{badge} me-2" data-latest-review>{label}</span>' in html
        assert all(
            f'id="record-{other.id}"' not in html
            for other in (first, second, third, fourth) if other != record
        )


def test_batch_review_does_not_save_valid_row_when_another_is_invalid(
    db_session, client, planner_user,
):
    add_source(db_session, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    first = ForecastDataRecord.query.filter_by(extract_id=extract.id).one()
    other = save_va05_extract(planner_user.id)
    foreign = ForecastDataRecord.query.filter_by(extract_id=other.id).one()
    login(client, planner_user.email, "Planner!Pass1234")
    url = f"{PAGE}/extracts/{extract.id}/reviews"
    first_review = {"record_id": first.id, "status": "accepted", "rationale": "Confirmed."}
    for rejected, identity in (
        ({**first_review, "record_id": foreign.id}, extract.capture_identity),
        (first_review, extract.capture_identity),
        (None, other.capture_identity),
    ):
        response = client.post(url, json={
            "capture_identity": identity,
            "reviews": [first_review, rejected] if rejected is not None else [first_review],
        })
        assert response.status_code == 400
        assert response.json["error"]
        assert ForecastDataDecision.query.count() == 0
    assert AuditLog.query.filter_by(action="forecast_data_decision_saved").count() == 0


def test_selected_page_reviews_only_target_reviewable_selected_rows(
    db_session, client, planner_user, viewer_user,
):
    for order_num in range(1, 4):
        add_source(db_session, order_num=order_num, release_qty=None)
    add_source(db_session, order_num=4)
    extract = save_va05_extract(planner_user.id)
    records = ForecastDataRecord.query.filter_by(extract_id=extract.id).order_by(
        ForecastDataRecord.id,
    ).all()
    save_data_decision(
        records[0].id, "investigate", "Earlier evidence.", planner_user.id,
        capture_identity=extract.capture_identity,
    )
    path = f"{PAGE}/extracts/{extract.id}"
    login(client, viewer_user.email, "Viewer!Pass1234")
    viewer_html = client.get(path + "?scope=all").get_data(as_text=True)
    assert 'id="selected-review"' not in viewer_html
    assert "data-review-select" not in viewer_html
    assert 'id="review-controls-link"' not in viewer_html
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    html = client.get(path + "?scope=all").get_data(as_text=True)
    assert 'id="selected-review"' in html
    assert 'id="review-toolbar" class="bg-body border rounded p-2 mb-2" tabindex="-1" hidden' in html
    assert 'href="#review-toolbar">Back to review controls</a>' in html
    assert html.count("data-review-select=") == 4
    assert f'data-review-select="{records[3].id}"' in html
    assert f'data-record-id="{records[3].id}"' not in html
    response = client.post(path + "/reviews", json={
        "capture_identity": extract.capture_identity,
        "reviews": [
            {"record_id": record.id, "status": "accepted", "rationale": "Shared evidence."}
            for record in (records[0], records[2])
        ],
    })
    assert response.status_code == 200
    decisions = ForecastDataDecision.query.order_by(ForecastDataDecision.id).all()
    assert [(d.record_id, d.status) for d in decisions] == [
        (records[0].id, "investigate"),
        (records[0].id, "accepted"),
        (records[2].id, "accepted"),
    ]


def test_finding_filter_combines_with_scope_and_preserves_review(
    db_session, client, planner_user,
):
    add_source(db_session, order_num=1, release_qty=None)
    add_source(db_session, order_num=2, release_price_gbp=None)
    add_source(
        db_session, order_num=3, release_qty=None, release_price_gbp=None,
    )
    extract = save_va05_extract(planner_user.id)
    login(client, planner_user.email, "Planner!Pass1234")
    path = f"{PAGE}/extracts/{extract.id}"

    response = client.get(path, query_string={
        "scope": "unreviewed", "finding": "missing_qty",
    })
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "2 matching saved records" in html
    assert "Order 1 / line 1" in html
    assert "Order 2 / line 1" not in html
    assert "Order 3 / line 1" in html
    assert 'name="finding" value="missing_qty"' in html
    summary = html.split('id="finding-summary">', 1)[1].split("</details>", 1)[0]
    assert "Findings by type" in summary
    assert "Missing main-row release quantity" in summary
    assert "Missing main-row GBP release value" in summary
    assert '<table class="table table-sm ai-findings-table ai-findings-with-planned mb-0">' in summary
    assert all(heading in summary for heading in (
        "Finding type", "Affected rows", "Reviewed", "Not reviewed", "Planned rows",
    ))
    assert summary.count("<span data-reviewed>0</span>") >= 2
    assert 'class="ai-finding-state ai-finding-current" data-finding-progress="missing_qty" data-total="2"' in summary
    assert f'data-unreviewed href="{path}?scope=unreviewed&amp;finding=missing_qty#saved-records"' in summary
    assert '<span data-no-unreviewed hidden>0</span>' in summary
    assert "A saved outcome counts as reviewed, not resolved." in summary
    finding_row = summary.split('data-finding-progress="missing_qty"', 1)[1].split("</tr>", 1)[0]
    assert 'data-finding-state data-state="attention"' in finding_row
    assert 'data-finding-status>Some rows not reviewed.' in finding_row
    assert 'data-finding-state data-state="clear"' in summary
    assert 'data-finding-status>No affected rows.' in summary
    assert html.index('id="finding-summary"') < html.index(
        'id="review-finding"',
    )
    record = ForecastDataRecord.query.filter_by(
        extract_id=extract.id, source_row_id=1,
    ).one()
    response = client.post(
        f"{PAGE}/records/{record.id}/review",
        data={
            "status": "investigate", "rationale": "Check quantity.",
            "scope": "unreviewed", "finding": "missing_qty", "page": "1",
            "capture_identity": extract.capture_identity,
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert "1 matching saved records" in html
    assert "<span data-reviewed>1</span>" in html
    finding_row = html.split('data-finding-progress="missing_qty"', 1)[1].split("</tr>", 1)[0]
    assert 'data-finding-state data-state="attention"' in finding_row
    assert "Order 1 / line 1" not in html
    assert "Order 3 / line 1" in html
    assert "scope=unreviewed" in response.request.url
    assert "finding=missing_qty" in response.request.url

    response = client.get(path, query_string={
        "scope": "all", "finding": "missing_value",
    })
    assert "2 matching saved records" in response.get_data(as_text=True)
    assert "Order 1 / line 1" not in response.get_data(as_text=True)
    response = client.get(path, query_string={"finding": "invalid"})
    assert response.status_code == 400
    assert client.post(
        f"{PAGE}/records/{record.id}/review",
        data={
            "status": "accepted", "rationale": "Evidence.",
            "finding": "invalid",
        },
    ).status_code == 400


def test_active_review_filters_can_be_removed_individually_or_cleared(
    db_session, client, planner_user,
):
    for order_num, voided, value, qty in (
        (1, True, 0, None), (2, True, 0, None),
        (3, False, 0, None), (4, False, 100, None),
    ):
        add_source(
            db_session, order_num=order_num, void_line=voided,
            release_price_gbp=Decimal(value), release_qty=qty,
        )
    extract = save_va05_extract(planner_user.id)
    records = ForecastDataRecord.query.filter_by(extract_id=extract.id).order_by(
        ForecastDataRecord.id,
    ).all()
    for record in (records[0], records[2], records[3]):
        save_data_decision(record.id, "accepted", "Reviewed.", planner_user.id)
    login(client, planner_user.email, "Planner!Pass1234")
    path = f"{PAGE}/extracts/{extract.id}"

    def clear_link(page, name):
        match = re.search(r'data-clear-filter="' + name + r'"[^>]*href="([^"]+)"', page)
        assert match is not None
        return html.unescape(match.group(1))

    page = client.get(path, query_string={
        "scope": "accepted", "finding": "nonpositive_value",
        "gbp_group": "void_zero", "per_page": 50,
    }).get_data(as_text=True)
    assert "Show: Accepted with evidence" in page
    assert "Finding: Nonpositive main-row GBP release value" in page
    assert "GBP group: Zero GBP, consistently void" in page

    for name, present, absent in (
        ("scope", (1, 2), (3, 4)),
        ("gbp_group", (1, 3), (2, 4)),
        ("finding", (1, 3, 4), (2,)),
        ("all", (1, 2, 3, 4), ()),
    ):
        link = clear_link(page, name)
        params = parse_qs(urlsplit(link).query)
        assert params["per_page"] == ["50"] and "page" not in params
        if name in ("finding", "all"):
            assert "gbp_group" not in params and "finding" not in params
        result = client.get(link).get_data(as_text=True)
        for num in present:
            assert f"Order {num} / line 1" in result
        for num in absent:
            assert f"Order {num} / line 1" not in result
    all_page = client.get(clear_link(page, "all")).get_data(as_text=True)
    assert "None (all saved rows)" in all_page

    release_page = client.get(path, query_string={
        "scope": "accepted", "finding": "nonpositive_value",
        "release": records[0].id, "per_page": 50,
    }).get_data(as_text=True)
    assert "Release: order 1 / line 1 / release 1" in release_page
    unfiltered_release = client.get(clear_link(release_page, "release")).get_data(as_text=True)
    assert "Order 1 / line 1" in unfiltered_release
    assert "Order 3 / line 1" in unfiltered_release


def test_finding_filter_paginates_matching_records_only(
    db_session, client, planner_user,
):
    for order_num in range(1, 29):
        add_source(
            db_session, order_num=order_num,
            release_qty=None if order_num <= 26 else Decimal("1"),
        )
    extract = save_va05_extract(planner_user.id)
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.get(
        f"{PAGE}/extracts/{extract.id}",
        query_string={"finding": "missing_qty", "page": 2},
    )
    html = response.get_data(as_text=True)
    assert "26 matching saved records" in html
    assert "Order 26 / line 1" in html
    assert "Order 27 / line 1" not in html
    assert "finding=missing_qty" in html
    assert "Page 2 of 2" in html
    last_record = ForecastDataRecord.query.filter_by(
        extract_id=extract.id, source_row_id=26,
    ).one()
    response = client.post(
        f"{PAGE}/records/{last_record.id}/review",
        data={
            "status": "investigate", "rationale": "Check quantity.",
            "scope": "unreviewed", "finding": "missing_qty", "page": "2",
            "capture_identity": extract.capture_identity,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "25 matching saved records" in response.get_data(as_text=True)
    assert "Order 1 / line 1" in response.get_data(as_text=True)
    assert "finding=missing_qty" in response.request.url


def test_review_page_size_pagination_and_batch_save(db_session, client, planner_user):
    for order_num in range(1, 103):
        add_source(db_session, order_num=order_num, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    records = ForecastDataRecord.query.filter_by(extract_id=extract.id).order_by(
        ForecastDataRecord.id,
    ).all()
    login(client, planner_user.email, "Planner!Pass1234")
    path = f"{PAGE}/extracts/{extract.id}"

    for size, expected_last in ((25, 25), (50, 50), (100, 100)):
        html = client.get(path, query_string={
            "scope": "unreviewed", "finding": "missing_qty", "per_page": size,
        }).get_data(as_text=True)
        assert html.count("data-review-row>") == size
        assert f"Order {expected_last} / line 1" in html
        assert f"Order {expected_last + 1} / line 1" not in html
        assert f'<option value="{size}" selected>' in html
        assert f'per_page={size}' in html
        assert 'aria-label="Saved record pages (top)"' in html
        assert 'aria-label="Saved record pages (bottom)"' in html
    response = client.get(path, query_string={
        "scope": "unreviewed", "finding": "missing_qty", "per_page": 50, "page": 2,
    })
    html = response.get_data(as_text=True)
    assert "Order 51 / line 1" in html and "Order 100 / line 1" in html
    assert 'per_page=50' in html and 'finding=missing_qty' in html
    assert "Page 2 of 3" in html
    assert client.get(path, query_string={"per_page": "invalid"}).status_code == 400
    assert client.get(path, query_string={"per_page": 1000}).status_code == 400
    response = client.get(path, query_string={"per_page": 100, "page": 99})
    assert response.status_code == 302 and "per_page=100" in response.location

    payload = {
        "capture_identity": extract.capture_identity,
        "reviews": [
            {"record_id": record.id, "status": "accepted", "rationale": "Checked."}
            for record in records[:100]
        ],
    }
    response = client.post(path + "/reviews", json=payload)
    assert response.status_code == 200 and len(response.json["reviews"]) == 100
    payload["reviews"].extend([
        {"record_id": records[100].id, "status": "accepted", "rationale": "Checked."},
    ])
    assert client.post(path + "/reviews", json=payload).status_code == 400


def test_single_review_preserves_selected_page_size(db_session, client, planner_user):
    add_source(db_session, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    record = ForecastDataRecord.query.filter_by(extract_id=extract.id).one()
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(f"{PAGE}/records/{record.id}/review", data={
        "status": "accepted", "rationale": "Checked.", "scope": "findings",
        "finding": "missing_qty", "page": 1, "per_page": 50,
        "capture_identity": extract.capture_identity,
    })
    assert response.status_code == 302
    assert "per_page=50" in response.location and "finding=missing_qty" in response.location


def test_larger_page_can_confirm_all_suggested_reviews(db_session, client, planner_user):
    for order_num in range(1, 27):
        add_source(db_session, order_num=order_num, release_qty=None)
    original = save_va05_extract(planner_user.id)
    old_rows = ForecastDataRecord.query.filter_by(extract_id=original.id).order_by(
        ForecastDataRecord.id,
    ).all()
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(f"{PAGE}/extracts/{original.id}/reviews", json={
        "capture_identity": original.capture_identity,
        "reviews": [
            {"record_id": row.id, "status": "accepted", "rationale": "Checked.", "remember": True}
            for row in old_rows
        ],
    })
    assert response.status_code == 200
    decisions = ForecastDataDecision.query.order_by(ForecastDataDecision.id).all()
    newer = save_va05_extract(planner_user.id)
    new_rows = ForecastDataRecord.query.filter_by(extract_id=newer.id).order_by(
        ForecastDataRecord.id,
    ).all()
    path = f"{PAGE}/extracts/{newer.id}"
    html = client.get(path, query_string={"scope": "unreviewed", "per_page": 50}).get_data(as_text=True)
    assert "Previously reviewed: 26 suggestion(s)" in html
    response = client.post(path + "/confirm-suggestions", data={
        "capture_identity": newer.capture_identity, "confirm": "yes", "per_page": 50,
        "suggestion": [f"{row.id}:{decision.id}" for row, decision in zip(new_rows, decisions)],
    })
    assert response.status_code == 302 and "per_page=50" in response.location
    assert ForecastDataDecision.query.filter(
        ForecastDataDecision.record_id.in_([row.id for row in new_rows]),
    ).count() == 26


def test_finding_filter_matches_exact_saved_rule(
    db_session, client, planner_user,
):
    add_source(db_session, order_num=1, release_qty=None)
    add_source(db_session, order_num=2, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    record = ForecastDataRecord.query.filter_by(
        extract_id=extract.id, source_row_id=2,
    ).one()
    record.findings = ["missing_qty_extra"]
    summary = dict(extract.summary)
    summary["rules"] = {
        **summary["rules"], "missing_qty_extra": "Other quantity finding",
    }
    extract.summary = summary
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")

    html = client.get(
        f"{PAGE}/extracts/{extract.id}",
        query_string={"finding": "missing_qty"},
    ).get_data(as_text=True)
    assert "1 matching saved records" in html
    assert "Order 1 / line 1" in html
    assert "Order 2 / line 1" not in html


def test_finding_filter_builds_postgresql_json_membership(db_session):
    bind = SimpleNamespace(dialect=postgresql.dialect())
    with patch.object(db_session, "get_bind", return_value=bind):
        condition = finding_matches("missing_qty")
    statement = sa.select(ForecastDataRecord.id).where(condition)
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "jsonb_array_elements_text" in sql
    assert "AS anon_1(value)" in sql


def test_missing_extracts_filters_and_cross_permission_access(
    db_session,
    client,
    purchasing_user,
    planner_user,
):
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    assert client.get(PAGE + "/extracts/1").status_code == 403
    assert client.post(PAGE + "/capture").status_code == 403
    client.get("/auth/logout")
    login(client, planner_user.email, "Planner!Pass1234")
    assert client.get(PAGE + "/extracts/999").status_code == 404
    assert client.post(PAGE + "/records/999/review").status_code == 404
    add_source(db_session, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    assert (
        client.get(
            f"{PAGE}/extracts/{extract.id}?scope=invalid",
        ).status_code
        == 400
    )


def test_extract_notes_are_append_only_and_protect_evidence(
    db_session, client, planner_user, viewer_user,
):
    add_source(db_session)
    extract = save_va05_extract(planner_user.id)
    path = f"{PAGE}/extracts/{extract.id}"
    identity = extract.capture_identity
    before_hash = extract.content_hash
    login(client, planner_user.email, "Planner!Pass1234")

    html = client.get(path).get_data(as_text=True)
    assert '<h2 id="extract-notes" class="h5">Business notes</h2>' in html
    note_form = {"capture_identity": identity}
    for body in ("  ", "x" * 2001):
        response = client.post(
            path + "/notes", data={**note_form, "body": body},
            follow_redirects=True,
        )
        assert "Enter a note between" in response.get_data(as_text=True)
    assert ForecastDataNote.query.count() == 0
    for body in ("  First observation <detail>  ", "Follow-up"):
        response = client.post(
            path + "/notes", data={**note_form, "body": body},
            follow_redirects=True,
        )
    html = response.get_data(as_text=True)
    assert "First observation &lt;detail&gt;" in html
    assert '<h2 id="extract-notes" class="h5">Business notes</h2>' in html
    assert html.index("Follow-up") < html.index("First observation")
    assert html.index('id="review-progress"') < html.index("Provenance and content hash") < html.index('id="extract-notes"')
    assert planner_user.username in html
    assert "Delete entire saved extract" not in html
    first = ForecastDataNote.query.order_by(ForecastDataNote.id).first()
    assert first.body == "First observation <detail>"
    assert first.author_id == planner_user.id and first.created_at
    assert ForecastDataDecision.query.count() == 0
    assert extract.content_hash == before_hash
    assert AuditLog.query.filter_by(
        action="forecast_data_note_added",
    ).count() == 2
    response = client.post(
        path + "/delete", data={**note_form, "confirm": "yes"},
        follow_redirects=True,
    )
    assert "Extracts with notes or row reviews cannot be deleted." in (
        response.get_data(as_text=True)
    )
    assert ForecastDataRecord.query.count() == 1

    client.get("/auth/logout")
    login(client, viewer_user.email, "Viewer!Pass1234")
    html = client.get(path).get_data(as_text=True)
    assert "First observation &lt;detail&gt;" in html
    assert "Add note" not in html
    assert client.post(path + "/notes", data=note_form).status_code == 403
    assert client.post(path + "/delete", data=note_form).status_code == 403


def test_feature_decisions_preserve_history_and_protect_extract(
    db_session, client, planner_user, viewer_user,
):
    add_source(db_session)
    extract = save_va05_extract(planner_user.id)
    other = save_va05_extract(planner_user.id)
    path = f"{PAGE}/extracts/{extract.id}"
    url = path + "/features"
    login(client, planner_user.email, "Planner!Pass1234")
    form = {
        "capture_identity": extract.capture_identity, "expected_id": "0",
        "feature": " Weekly GBP ", "decision": "test", "availability": "known",
        "coverage": "Completed weeks", "evidence": "Time patterns <lag 1>",
        "cutoff_details": "Prior completed week", "rationale": "Candidate baseline",
    }
    for bad in (
        {"feature": " "}, {"decision": "approve"}, {"availability": "maybe"},
        {"coverage": ""}, {"evidence": "x" * 2001}, {"cutoff_details": ""},
        {"rationale": ""}, {"expected_id": "invalid"}, {"capture_identity": "old"},
    ):
        failed = client.post(url, data={**form, **bad}, follow_redirects=True)
        assert failed.status_code == 400
        if "rationale" not in bad:
            assert "Candidate baseline" in failed.get_data(as_text=True)
        assert "Decision not saved" in failed.get_data(as_text=True)
    assert ForecastFeatureDecision.query.count() == 0

    response = client.post(url, data=form, follow_redirects=True)
    html = response.get_data(as_text=True)
    first = ForecastFeatureDecision.query.one()
    assert first.feature == "Weekly GBP" and first.author_id == planner_user.id
    assert "Time patterns &lt;lag 1&gt;" in html
    assert "not data approval or model configuration" in html
    assert "Weekly GBP" not in client.get(f"{PAGE}/extracts/{other.id}/features").get_data(as_text=True)
    assert AuditLog.query.filter_by(action="forecast_feature_decision_saved").count() == 1
    assert "Delete entire saved extract" not in client.get(path).get_data(as_text=True)
    response = client.post(path + "/delete", data={
        "capture_identity": extract.capture_identity, "confirm": "yes",
    }, follow_redirects=True)
    assert "Extracts with feature decisions cannot be deleted." in response.get_data(as_text=True)

    response = client.post(url, data={**form, "feature": "weekly gbp"}, follow_redirects=True)
    assert "already exists" in response.get_data(as_text=True)
    response = client.post(url, data={
        **form, "feature": "weekly gbp", "expected_id": str(first.id),
        "decision": "defer", "availability": "unknown", "rationale": "Check availability",
    }, follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "Check availability" in html and "Candidate baseline" in html
    assert "Earlier decisions (1)" in html
    assert ForecastFeatureDecision.query.count() == 2
    response = client.post(url, data={**form, "expected_id": str(first.id)}, follow_redirects=True)
    assert "feature has changed" in response.get_data(as_text=True)
    assert "Candidate baseline" in response.get_data(as_text=True)
    assert ForecastFeatureDecision.query.count() == 2
    assert ForecastDataRecord.query.filter_by(extract_id=extract.id).count() == 1
    assert AuditLog.query.filter_by(action="forecast_feature_decision_saved").count() == 2

    client.get("/auth/logout")
    login(client, viewer_user.email, "Viewer!Pass1234")
    html = client.get(url).get_data(as_text=True)
    assert "Check availability" in html and "Record an updated decision" not in html
    assert client.post(url, data=form).status_code == 403


def test_feature_decisions_migration_round_trip():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "f32b461c9a70_add_forecast_feature_decisions.py"
    migration = runpy.run_path(str(path))
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE forecast_data_extracts (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO forecast_data_extracts VALUES (1)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            table = sa.Table("forecast_feature_decisions", sa.MetaData(), autoload_with=connection)
            connection.execute(table.insert().values(
                extract_id=1, feature="Weekly GBP", feature_key="weekly gbp",
                decision="test", availability="known", coverage="History", evidence="EDA",
                cutoff_details="Prior week", rationale="Baseline", created_at=date(2026, 11, 25),
            ))
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(sa.text("DELETE FROM forecast_data_extracts WHERE id = 1"))
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(table.update().values(decision="approve"))
            migration["downgrade"]()
            assert "forecast_feature_decisions" not in sa.inspect(connection).get_table_names()
    engine.dispose()


def test_reusable_reviews_migration_preserves_existing_decisions():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "c51e7b20d931_add_reusable_data_reviews.py"
    migration = runpy.run_path(str(path))
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE forecast_data_decisions (id INTEGER PRIMARY KEY, rationale TEXT NOT NULL)"))
        connection.execute(sa.text("INSERT INTO forecast_data_decisions VALUES (1, 'Existing evidence')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            result = connection.execute(sa.text(
                "SELECT rationale, remember, reused_from_id FROM forecast_data_decisions WHERE id = 1"
            )).one()
            assert tuple(result) == ("Existing evidence", 0, None)
            connection.execute(sa.text(
                "INSERT INTO forecast_data_decisions (id, rationale, remember, reused_from_id) VALUES (2, 'Copy', 1, 1)"
            ))
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(sa.text("DELETE FROM forecast_data_decisions WHERE id = 1"))
            connection.execute(sa.text("DELETE FROM forecast_data_decisions WHERE id = 2"))
            migration["downgrade"]()
            assert connection.execute(sa.text("SELECT rationale FROM forecast_data_decisions WHERE id = 1")).scalar_one() == "Existing evidence"
    engine.dispose()


def test_confirmed_deletion_only_removes_unused_saved_extract(
    db_session, client, planner_user,
):
    add_source(db_session, release_qty=None)
    protected = save_va05_extract(planner_user.id)
    target = save_va05_extract(planner_user.id)
    protected_id, target_id = protected.id, target.id
    protected_record = ForecastDataRecord.query.filter_by(
        extract_id=protected_id,
    ).one()
    save_data_decision(
        protected_record.id, "investigate", "Keep evidence.", planner_user.id,
    )
    login(client, planner_user.email, "Planner!Pass1234")
    target_path = f"{PAGE}/extracts/{target_id}"
    protected_path = f"{PAGE}/extracts/{protected_id}"
    form = {"capture_identity": target.capture_identity}
    html = client.get(target_path).get_data(as_text=True)
    assert "Delete entire saved extract" in html
    assert client.get(target_path + "/delete").status_code == 405
    for bad_form in (form, {"capture_identity": "old", "confirm": "yes"}):
        response = client.post(
            target_path + "/delete", data=bad_form, follow_redirects=True,
        )
        assert response.request.path == target_path
    response = client.post(
        protected_path + "/delete",
        data={
            "capture_identity": protected.capture_identity, "confirm": "yes",
        },
        follow_redirects=True,
    )
    assert "Extracts with notes or row reviews cannot be deleted." in (
        response.get_data(as_text=True)
    )
    assert ForecastDataExtract.query.count() == 2

    response = client.post(
        target_path + "/delete", data={**form, "confirm": "yes"},
        follow_redirects=True,
    )
    assert response.request.path == PAGE
    assert "Saved extract deleted." in response.get_data(as_text=True)
    assert client.get(target_path).status_code == 404
    assert ForecastDataRecord.query.one().extract_id == protected_id
    assert SalesOrder.query.count() == ForecastDataDecision.query.count() == 1
    audit = AuditLog.query.filter_by(
        action="forecast_data_extract_deleted",
    ).one()
    assert json.loads(audit.details)["extract_id"] == target_id

    replacement = save_va05_extract(planner_user.id)
    replacement_path = f"{PAGE}/extracts/{replacement.id}"
    replacement_record = ForecastDataRecord.query.filter_by(
        extract_id=replacement.id,
    ).one()
    # SQLite can reuse IDs after deletion. Stale forms must not affect a
    # different capture even if their URL happens to match.
    for path, data in (
        (replacement_path + "/delete", {"confirm": "yes"}),
        (replacement_path + "/notes", {"body": "Stale note."}),
        (f"{PAGE}/records/{replacement_record.id}/review",
         {"status": "accepted", "rationale": "Stale review."}),
    ):
        response = client.post(
            path, data={**form, **data}, follow_redirects=True,
        )
        assert "The extract has changed." in response.get_data(as_text=True)
    assert ForecastDataExtract.query.count() == 2
    assert ForecastDataNote.query.count() == 0
    assert ForecastDataDecision.query.count() == 1


def test_capture_review_notes_and_deletion_require_csrf(
    app, db_session, client, planner_user
):
    add_source(db_session, release_qty=None)
    extract = save_va05_extract(planner_user.id)
    record = ForecastDataRecord.query.one()
    login(client, planner_user.email, "Planner!Pass1234")
    with app.app_context():
        old_setting = app.config["WTF_CSRF_ENABLED"]
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            assert client.post(PAGE + "/capture").status_code == 400
            for action in ("notes", "delete", "reviews", "finding-reviews", "features"):
                assert client.post(
                    f"{PAGE}/extracts/{extract.id}/{action}",
                ).status_code == 400
            assert (
                client.post(
                    f"{PAGE}/records/{record.id}/review",
                    data={"status": "accepted", "rationale": "Evidence."},
                ).status_code
                == 400
            )
        finally:
            app.config["WTF_CSRF_ENABLED"] = old_setting
    assert ForecastDataExtract.query.count() == 1
    assert ForecastDataDecision.query.count() == 0
    assert ForecastDataNote.query.count() == 0


def test_saved_extract_pagination_keeps_unflagged_rows(
    db_session,
    client,
    planner_user,
):
    for order_num in range(1, 28):
        add_source(db_session, order_num=order_num)
    extract = save_va05_extract(planner_user.id)
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.get(
        f"{PAGE}/extracts/{extract.id}?scope=all&page=2",
    )
    assert response.status_code == 200
    assert "Order 27 / line 1" in response.get_data(as_text=True)
    assert "No findings under" in response.get_data(as_text=True)
    delete_url = f'action="{PAGE}/extracts/{extract.id}/delete"'
    assert response.get_data(as_text=True).count(delete_url) == 1
    html = client.get(f"{PAGE}/extracts/{extract.id}").get_data(as_text=True)
    assert "No saved rows match this filter" in html
    assert html.count(delete_url) == 1
    assert extract.row_count == 27


def test_data_review_migration_round_trip():
    migration_path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "c92e7a14d6b8_add_forecast_data_reviews.py"
    )
    migration = runpy.run_path(str(migration_path))
    notes_migration = runpy.run_path(str(migration_path.with_name(
        "e6d1c5f8a237_add_forecast_data_notes.py",
    )))
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(
            sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            connection.execute(sa.text("INSERT INTO users (id) VALUES (1)"))
            extracts = sa.Table(
                "forecast_data_extracts",
                sa.MetaData(),
                autoload_with=connection,
            )
            connection.execute(
                extracts.insert().values(
                    id=1,
                    captured_at=date(2026, 11, 18),
                    captured_by_id=1,
                    source="bskyVA05v1",
                    rules_version="test",
                    row_count=0,
                    content_hash="a" * 64,
                    source_settings={},
                    summary={},
                )
            )
            assert (
                connection.execute(
                    sa.select(extracts.c.source),
                ).scalar_one()
                == "bskyVA05v1"
            )
            notes_migration["upgrade"]()
            notes = sa.Table(
                "forecast_data_notes", sa.MetaData(), autoload_with=connection,
            )
            connection.execute(notes.insert().values(
                extract_id=1, body="Retain this extract.", author_id=1,
                created_at=date(2026, 11, 18),
            ))
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(extracts.delete().where(extracts.c.id == 1))
            notes_migration["downgrade"]()
            assert connection.scalar(sa.select(extracts.c.id)) == 1
            migration["downgrade"]()
            assert sa.inspect(connection).get_table_names() == ["users"]
    engine.dispose()
