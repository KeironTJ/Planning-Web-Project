"""Retained manifest sync, correction safety, history and shipped fallback."""

import importlib.util
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app.auth.models import User
from app.core.epicor_importers import REGISTRY
from app.sales.orders.models import ImportBatch
from app.transport.load_board import get_load_board
from app.transport.manifest_history import get_manifest_history
from app.transport.manifest_importer import TransportManifestImporter
from app.transport.models import TransportManifest, TransportManifestRelease
from tests.test_transport_loads import baq_record, login_admin, sync
from tests.test_transport_readiness import record, sync_orders


@pytest.fixture(autouse=True)
def ctx(app):
    with app.app_context():
        yield


def manifest_row(load="LOAD-1", order=10001, **overrides):
    return {
        "UD110_Key1": load, "UD110_Company": "TEST",
        "UD110_Character02": "North", "UD110_Character03": "Shipped",
        "UD110_Date01": "2026-09-30T00:00:00Z",
        "UD110_Date02": "2026-10-01T00:00:00Z",
        "OrderHed_OrderNum": order, "OrderRel_OrderLine": 1, "OrderRel_OrderRelNum": 1,
        "Customer_Name": "Historic customer", "OrderHed_PONum": "PO-123",
        "OrderRel_PartNum": "SOFA", "Part_PartDescription": "Historic sofa",
        "JobProd_JobNum": "JOB-1", "ShipDtl_PackNum": 123, "ShipDtl_PackLine": 1,
        "RowIdent": f"{load}-{order}", **overrides,
    }


def sync_manifests(records, **kwargs):
    client = Mock()
    client.get_baq.return_value = records
    with patch.object(TransportManifestImporter, "get_dynamic_params", return_value={
        "DateFrom": "2026-09-29", "DateBefore": "2026-10-04",
    }):
        batch = TransportManifestImporter(client).run(**kwargs)
    return batch, client


def shipped_load():
    return get_load_board()["stages"][3]["loads"][0]


def test_registry_dedup_and_exact_pack_lines():
    first = manifest_row()
    other = manifest_row(JobProd_JobNum="JOB-2", ShipDtl_PackLine=2)
    batch, client = sync_manifests([first, first.copy(), other, manifest_row(order=10002)])
    assert REGISTRY["transport_manifest"] is TransportManifestImporter
    assert batch.row_count == 4
    assert batch.rows_inserted == 1
    client.get_baq.assert_called_once_with(
        "CSGTransManifestTransportAPI", params={
            **TransportManifestImporter.BAQ_PARAMS,
            "DateFrom": "2026-09-29", "DateBefore": "2026-10-04",
        }, page_size=2000,
    )
    saved = TransportManifest.query.one()
    assert saved.source_status == "SHIPPED"
    assert saved.ship_date == date(2026, 10, 1)
    assert len(saved.releases) == 2
    release = saved.releases[0]
    assert release.jobs == ["JOB-1", "JOB-2"]
    assert release.packs == [{"number": 123, "line": 1}, {"number": 123, "line": 2}]
    assert release.customer_po == "PO-123"
    assert saved.first_observed_at == saved.contents_observed_at == saved.last_seen_at
    assert not hasattr(release, "quantity")
    assert not hasattr(release, "production_state")


def test_retains_absent_loads_and_replaces_returned_contents_idempotently():
    sync_manifests([manifest_row(), manifest_row("OLD-LOAD")])
    first_seen = TransportManifest.query.filter_by(load_id="LOAD-1").one().first_observed_at
    correction = manifest_row(order=20002, Customer_Name="Corrected")
    batch, _ = sync_manifests([correction])
    assert batch.rows_updated == 1
    assert TransportManifest.query.count() == 2
    saved = TransportManifest.query.filter_by(load_id="LOAD-1").one()
    assert saved.first_observed_at == first_seen
    assert saved.contents_observed_at > first_seen
    assert [release.order_num for release in saved.releases] == [20002]
    sync_manifests([correction])
    assert TransportManifestRelease.query.count() == 2
    assert TransportManifest.query.filter_by(load_id="OLD-LOAD").one().releases[0].order_num == 10001


def test_non_shipped_excluded_and_status_correction_preserves_saved_contents():
    sync_manifests([manifest_row(), manifest_row("SCHEDULED", UD110_Character03="Scheduled")])
    saved = TransportManifest.query.one()
    observed = saved.contents_observed_at
    sync_manifests([manifest_row(UD110_Character03="Scheduled", Customer_Name="Not shipped")])
    assert saved.source_status == "SCHEDULED"
    assert saved.last_seen_at > observed
    assert saved.contents_observed_at == observed
    assert saved.releases[0].customer == "Historic customer"
    sync([baq_record(status="Shipped")])
    assert shipped_load()["manifest"] is None
    sync_manifests([manifest_row(Customer_Name="Corrected shipped")])
    assert shipped_load()["manifest"].releases[0].customer == "Corrected shipped"


@pytest.mark.parametrize("overrides", [
    {"UD110_Key1": ""}, {"OrderHed_OrderNum": 0}, {"OrderRel_OrderLine": "nan"},
    {"OrderRel_OrderRelNum": 1.5}, {"UD110_Date02": "bad"},
    {"ShipDtl_PackNum": -1}, {"ShipDtl_PackNum": None, "ShipDtl_PackLine": 1},
    {"UD110_Character03": ""},
])
def test_invalid_data_rolls_back_and_records_failure(overrides):
    sync_manifests([manifest_row(), manifest_row("SECOND")])
    with pytest.raises(ValueError, match="CSGTransManifestTransport"):
        sync_manifests([manifest_row(Customer_Name="Would change"), manifest_row("SECOND", **overrides)])
    assert TransportManifest.query.count() == 2
    assert TransportManifest.query.filter_by(load_id="LOAD-1").one().releases[0].customer == "Historic customer"
    assert ImportBatch.query.order_by(ImportBatch.id.desc()).first().status == "failed"


@pytest.mark.parametrize("overrides", [
    {"Customer_Name": "Conflicting"}, {"OrderRel_PartNum": "OTHER"},
    {"Part_PartDescription": "OTHER"}, {"UD110_Date02": "2026-10-02"},
    {"UD110_Character02": "South"}, {"UD110_Character03": "Scheduled"},
    {"OrderHed_PONum": "OTHER"},
])
def test_conflicts_abort_import(overrides):
    sync_manifests([manifest_row()])
    with pytest.raises(ValueError, match="conflicting"):
        sync_manifests([manifest_row(), manifest_row(**overrides)])
    assert TransportManifestRelease.query.one().customer == "Historic customer"


def test_empty_results_and_fetch_failures_preserve_history():
    sync_manifests([manifest_row()])
    empty, _ = sync_manifests([])
    assert empty.status == "success"
    assert empty.rows_inserted == empty.rows_updated == 0
    client = Mock()
    client.get_baq.side_effect = RuntimeError("API unavailable")
    with pytest.raises(RuntimeError, match="API unavailable"):
        TransportManifestImporter(client).run()
    assert TransportManifest.query.count() == 1
    assert ImportBatch.query.filter_by(status="failed").count() == 1


def test_default_window_includes_day_90_and_explicit_override():
    today = date.today()
    params = TransportManifestImporter(Mock()).get_dynamic_params()
    assert params == {
        "DateFrom": (today - timedelta(days=90)).isoformat(),
        "DateBefore": (today + timedelta(days=91)).isoformat(),
    }
    batch, client = sync_manifests(
        [manifest_row(UD110_Date02="2024-03-01")],
        params={"DateFrom": "2024-03-01", "DateBefore": "2024-03-02"},
    )
    assert client.get_baq.call_args.kwargs["params"]["DateFrom"] == "2024-03-01"
    assert "[2024-03-01, 2024-03-02)" in batch.notes


@pytest.mark.parametrize("params", [
    {"DateFrom": "bad"}, {"DateBefore": ""},
    {"DateFrom": "2026-10-01", "DateBefore": "2026-10-01"},
    {"DateFrom": "2026-10-02", "DateBefore": "2026-10-01"},
])
def test_invalid_parameter_dates_fail_before_fetch(params):
    with pytest.raises(ValueError, match="CSGTransManifestTransportAPI"):
        sync_manifests([], params=params)


@pytest.mark.parametrize("header_date", ["2026-09-30", "2026-10-02", None])
def test_out_of_window_response_rejected_without_modifying_history(header_date):
    sync_manifests([manifest_row()])
    with pytest.raises(ValueError, match="outside requested window"):
        sync_manifests([manifest_row(UD110_Date02=header_date)],
                      params={"DateFrom": "2026-10-01", "DateBefore": "2026-10-02"})
    assert TransportManifest.query.one().ship_date == date(2026, 10, 1)


def test_filtered_sync_cannot_replace_a_load_with_partial_contents():
    sync_manifests([manifest_row(), manifest_row(order=10002)])
    with pytest.raises(ValueError, match="complete load contents"):
        sync_manifests([manifest_row()], params={"$filter": "OrderHed_OrderNum eq 10001"})
    assert TransportManifestRelease.query.count() == 2


def test_missing_packs_and_future_header_date_not_treated_as_dispatch():
    sync_manifests([manifest_row(ShipDtl_PackNum=None, ShipDtl_PackLine=None,
                                UD110_Date02="2026-12-31T00:00:00+01:00")],
                   params={"DateFrom": "2026-12-31", "DateBefore": "2027-01-01"})
    saved = TransportManifest.query.one()
    assert saved.ship_date == date(2026, 12, 31)
    assert saved.releases[0].packs == []


def test_date_slices_restart_pagination_and_preserve_all_contents():
    client = Mock()
    client.get_baq.side_effect = [
        [manifest_row(UD110_Date02="2026-09-01")],
        [manifest_row("SECOND", UD110_Date02="2026-09-08")],
        [],
    ]
    batch = TransportManifestImporter(client).run(
        params={"DateFrom": "2026-09-01", "DateBefore": "2026-09-16"},
    )
    assert batch.rows_inserted == 2
    assert batch.row_count == 2
    assert [(call.kwargs["params"]["DateFrom"], call.kwargs["params"]["DateBefore"])
            for call in client.get_baq.call_args_list] == [
        ("2026-09-01", "2026-09-08"), ("2026-09-08", "2026-09-15"),
        ("2026-09-15", "2026-09-16"),
    ]


def test_later_slice_failure_preserves_all_previous_history():
    sync_manifests([manifest_row()])
    client = Mock()
    client.get_baq.side_effect = [
        [manifest_row(Customer_Name="Would change", UD110_Date02="2026-09-01")],
        RuntimeError("Second slice unavailable"),
    ]
    with pytest.raises(RuntimeError, match="Second slice"):
        TransportManifestImporter(client).run(
            params={"DateFrom": "2026-09-01", "DateBefore": "2026-09-16"},
        )
    assert TransportManifestRelease.query.one().customer == "Historic customer"


def test_fallback_only_shipped_without_current_details_and_readiness_unchanged():
    sync([baq_record(status="Shipped"), baq_record("PLANNED", status="Planned")])
    before = shipped_load()["readiness"]
    sync_manifests([manifest_row(), manifest_row("PLANNED")])
    board = get_load_board()
    assert board["stages"][0]["loads"][0]["manifest"] is None
    load = board["stages"][3]["loads"][0]
    assert load["manifest"].load_id == "LOAD-1"
    assert load["readiness"] == before
    sync_orders([record()])
    assert shipped_load()["manifest"] is None
    assert shipped_load()["readiness"]["count"] == 1


def test_history_filters_and_pagination():
    sync_manifests([manifest_row(f"LOAD-{n:02}", order=10000 + n) for n in range(25)])
    result = get_manifest_history(page=1)
    assert result["pagination"].total == 25
    assert len(result["manifests"]) == 20
    assert len(get_manifest_history(page=2)["manifests"]) == 5
    assert get_manifest_history(search="10003")["pagination"].total == 1
    assert get_manifest_history(search="historic customer")["pagination"].total == 25
    assert get_manifest_history(search="PO-123")["pagination"].total == 25
    assert get_manifest_history(search="SOFA")["pagination"].total == 25
    assert get_manifest_history(route="South")["pagination"].total == 0
    assert get_manifest_history(ship_from=date(2026, 10, 2))["pagination"].total == 0
    assert get_manifest_history(ship_to=date(2026, 9, 30))["pagination"].total == 0


def test_history_rendering_fallback_escaping_and_sync_warning(client, admin_user):
    sync([baq_record(status="Shipped")])
    sync_manifests([manifest_row(Customer_Name="<script>alert(1)</script>")])
    login_admin(client)
    history = client.get("/transport/manifest-history")
    assert history.status_code == 200
    html = history.get_data(as_text=True)
    assert "Scheduled ship header date" in html
    assert "not verified dispatch dates" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "123 / 1" in html
    board = client.get("/transport/loads").get_data(as_text=True)
    assert "Historical order releases" in board
    assert "Historic sofa" in board
    assert "does not supply shipped units" in board
    api = Mock()
    api.get_baq.side_effect = RuntimeError("API unavailable")
    with pytest.raises(RuntimeError):
        TransportManifestImporter(api).run()
    assert "latest manifest sync failed" in client.get("/transport/manifest-history").get_data(as_text=True)
    assert "latest manifest sync failed" in client.get("/transport/loads").get_data(as_text=True)
    sync_manifests([manifest_row(UD110_Character03="Scheduled")])
    assert "Previous shipped contents retained" in client.get("/transport/manifest-history").get_data(as_text=True)


def test_current_contents_take_precedence_in_html(client, admin_user):
    sync([baq_record(status="Shipped")])
    sync_manifests([manifest_row()])
    sync_orders([record(Customer_Name="Current customer")])
    login_admin(client)
    html = client.get("/transport/loads").get_data(as_text=True)
    assert "Current customer" in html
    assert "Historic customer" not in html
    assert "Historical order releases" not in html


@pytest.mark.parametrize("query", ["page=0", "page=bad", "ship_from=bad",
                                    "ship_from=2026-10-02&ship_to=2026-10-01"])
def test_invalid_history_filters_rejected(client, admin_user, query):
    login_admin(client)
    assert client.get(f"/transport/manifest-history?{query}").status_code == 400


def test_history_requires_login_and_transport_permission(client, db_session):
    assert client.get("/transport/manifest-history").status_code == 302
    user = User(username="no_transport", email="none@test.com", is_active=True)
    user.set_password("NoTransport!123")
    db_session.add(user)
    db_session.commit()
    client.post("/auth/login", data={"login": user.email, "password": "NoTransport!123"})
    assert client.get("/transport/manifest-history").status_code == 403


def test_manifest_migration_upgrade_and_downgrade():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "d7f924bc3810_add_retained_transport_manifests.py"
    spec = importlib.util.spec_from_file_location("manifest_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            inspector = sa.inspect(connection)
            assert set(inspector.get_table_names()) == {"transport_manifests", "transport_manifest_releases"}
            assert {column["name"] for column in inspector.get_columns("transport_manifests")} == {
                column.name for column in TransportManifest.__table__.columns
            }
            assert inspector.get_unique_constraints("transport_manifest_releases")[0]["column_names"] == [
                "manifest_id", "order_num", "order_line", "rel_num",
            ]
            migration.downgrade()
            assert sa.inspect(connection).get_table_names() == []
    engine.dispose()
