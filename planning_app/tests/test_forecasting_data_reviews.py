import hashlib
import json
import runpy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.auth.models import AuditLog
from app.core.exceptions import NotFoundError, ValidationError
from app.forecasting.data_quality import (
    save_data_decision,
    save_va05_extract,
)
from app.forecasting.models import (
    ForecastDataDecision,
    ForecastDataExtract,
    ForecastDataRecord,
)
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
            "repeated_release",
            "conflicting_qty",
            "conflicting_value",
            "conflicting_received",
        }
    assert set(rows[3].findings) == {"missing_main", "conflicting_received"}
    assert "nonpositive_qty" not in extract.summary["counts"]
    assert SalesOrder.query.count() == 4


def test_consistent_main_repeats_are_not_quantity_conflicts(
    db_session,
    planner_user,
):
    add_source(db_session)
    add_source(db_session, assembly_seq=None, job_num="J2")
    extract = save_va05_extract(planner_user.id)
    assert extract.summary["counts"] == {"repeated_release": 2}


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


def test_capture_and_review_require_csrf(
    app, db_session, client, planner_user
):
    add_source(db_session, release_qty=None)
    save_va05_extract(planner_user.id)
    record = ForecastDataRecord.query.one()
    login(client, planner_user.email, "Planner!Pass1234")
    with app.app_context():
        old_setting = app.config["WTF_CSRF_ENABLED"]
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            assert client.post(PAGE + "/capture").status_code == 400
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
    assert extract.row_count == 27


def test_data_review_migration_round_trip():
    migration_path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "c92e7a14d6b8_add_forecast_data_reviews.py"
    )
    migration = runpy.run_path(str(migration_path))
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
            migration["downgrade"]()
            assert sa.inspect(connection).get_table_names() == ["users"]
    engine.dispose()
