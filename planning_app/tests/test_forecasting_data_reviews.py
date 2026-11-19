import hashlib
import json
import runpy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql

from app.auth.models import AuditLog
from app.core.exceptions import NotFoundError, ValidationError
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
)
from app.forecasting.services import (
    save_model_category,
    save_model_classification,
    save_model_classification_updates,
)
from app.forecasting.weekly_intake import explore_weekly_intake
from app.sales.orders.models import SalesOrder

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
    assert "120.00 GBP" in html
    assert "999.00" not in html
    assert "Partial week" in html and "Possible shutdown" in html
    assert "No usable observations" in html
    assert client.get(f"{PAGE}/extracts/{extract.id}").get_data(as_text=True).count(
        path,
    ) == 1
    unavailable = client.get(f"{PAGE}/extracts/{legacy.id}/weekly-intake")
    assert unavailable.status_code == 200
    assert "including the void flag" in unavailable.get_data(as_text=True)
    assert "120.00 GBP" not in unavailable.get_data(as_text=True)


def test_extract_is_reproducible_and_survives_operational_refresh(
    db_session,
    planner_user,
):
    source = add_source(
        db_session,
        release_qty=None,
        release_price_gbp=Decimal("0"),
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
        assert f"VA05 Data Review {reference}" in review.get_data(as_text=True)


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
        assert f"assembly {assembly}" in html
    assert "Model: SOFA" in html and "Model: SUB-6" in html
    assert "Model: OTHER-" not in html
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
    assert "Model: SOFA" in html
    assert "Model: SCATTER" in html
    assert "Model: sofa" in html
    assert "Model: Blank" in html
    assert "Current model category: Sofas" in html
    assert html.count("Current model category: Scatters") == 2
    assert "Current model category: Unclassified" in html
    assert "not historical assignments" in html

    save_model_classification("SOFA", scatters.id, planner_user.id)
    SalesOrder.query.delete()
    db_session.commit()
    html = client.get(
        f"{PAGE}/extracts/{extract.id}",
    ).get_data(as_text=True)
    assert html.count("Current model category: Scatters") == 3
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
    assert "Model: Not captured in this extract" in html
    assert "Model: LATER" not in html
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
    response = client.post(PAGE + "/capture", follow_redirects=True)
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
    summary = html.split(
        '<section class="card mb-3" aria-labelledby="finding-summary">', 1,
    )[1]
    summary = summary.split("</section>", 1)[0]
    assert "Findings summary" in summary
    assert "Missing main-row release quantity" in summary
    assert "Missing main-row GBP release value" in summary
    assert summary.count("<strong>2</strong>") == 2
    assert "affected rows across the full extract" in summary.lower()
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
    assert html.index("Follow-up") < html.index("First observation")
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
            for action in ("notes", "delete"):
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
