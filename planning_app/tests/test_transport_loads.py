"""Transport snapshot mapping, visual semantics, filters and access."""

from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.auth.models import User
from app.core.epicor_importers import REGISTRY
from app.sales.orders.models import ImportBatch
from app.transport.importer import TransportLoadImporter
from app.transport.load_board import get_load_board
from app.transport.models import TransportLoad


@pytest.fixture(autouse=True)
def ctx(app):
    with app.app_context():
        yield


def baq_record(load_id="LOAD-1", status="Scheduled", **overrides):
    return {
        "SysRowID": load_id, "UD110_Key1": load_id,
        "UD110_Character01": "TRANSPORT-CODE", "UD110_Character02": "Edmonsons",
        "UD110_Character08": "VEHICLE-1", "UD110_Character03": status,
        "UD110_Number05": 100, "UD110_Number06": 4.5,
        "UD110_ShortChar04": "SEATS", "Calculated_Percentage": 4.5,
        "Calculated_OrderQty": 25, "Calculated_OrderVal": 21546,
        "UD110_Date01": "2026-10-30T00:00:00+01:00",
        "UD110_Date02": "2026-10-31T00:00:00Z",
        "UD110_Date03": "2026-11-03T00:00:00Z",
        "Calculated_Calculated_LoadTimeCalc": "08:30:00",
        "Calculated_Calculated_ShipTimeCalc": "00:00:00",
        "Calculated_Calculated_ReturnTimeCalc": "10:15:00",
        **overrides,
    }


def sync(records):
    client = Mock()
    client.get_baq.return_value = records
    return TransportLoadImporter(client).run()


def login_admin(client):
    client.post("/auth/login", data={
        "login": "admin@test.com", "password": "Admin!Pass1234",
    })


def test_registry_and_field_mapping():
    assert REGISTRY["transport_loads"] is TransportLoadImporter
    batch = sync([baq_record(status=" Packed ")])
    load = TransportLoad.query.one()
    assert batch.status == ImportBatch.STATUS_SUCCESS
    assert batch.rows_inserted == 1
    assert load.load_id == "LOAD-1"
    assert load.transport_code == "TRANSPORT-CODE"
    assert load.route == "Edmonsons"
    assert load.vehicle == "VEHICLE-1"
    assert load.status == "PACKED"
    assert load.capacity == Decimal("100")
    assert load.remaining == Decimal("4.5")
    assert load.available_pct == Decimal("4.5")
    assert load.order_qty == Decimal("25")
    assert load.order_value == Decimal("21546")
    assert load.load_date == date(2026, 10, 30)
    assert load.ship_date == date(2026, 10, 31)
    assert load.return_date == date(2026, 11, 3)
    assert load.capacity_uom == "SEATS"
    assert load.load_time == "08:30:00"


def test_capacity_is_not_status_progress():
    sync([baq_record(status="Planned")])
    board = get_load_board(today=date(2026, 10, 2))
    load = board["stages"][0]["loads"][0]
    assert load["used_pct"] == Decimal("95.5")
    assert load["available_pct"] == Decimal("4.5")
    assert load["bar_pct"] == 95.5
    assert board["active_count"] == 1


def test_counts_values_and_dates_share_filters():
    sync([
        baq_record("PLANNED", "Planned", Calculated_OrderQty=0, Calculated_OrderVal=0),
        baq_record("SCHEDULED", "Scheduled", Calculated_OrderQty=29, Calculated_OrderVal=25930),
        baq_record("PACKED", "Packed"),
        baq_record("SHIPPED", "Shipped", UD110_Character02="Courier UK"),
    ])
    board = get_load_board(today=date(2026, 10, 31))
    assert [stage["key"] for stage in board["stages"]] == ["PLANNED", "SCHEDULED", "PACKED", "SHIPPED"]
    assert [stage["count"] for stage in board["stages"]] == [1, 1, 1, 1]
    assert board["summary"]["count"] == 4
    assert board["summary"]["value"] == Decimal("69022")
    assert board["summary"]["quantity"] == Decimal("79")
    assert board["active_count"] == 3
    assert board["today_count"] == 3
    assert board["overdue_count"] == 0
    assert board["timeline"]["labels"] == ["31 Oct 2026"]
    assert [item["data"] for item in board["timeline"]["datasets"]] == [[1], [1], [1], [1]]
    filtered = get_load_board(route="Edmonsons", ship_from=date(2026, 10, 31), ship_to=date(2026, 10, 31))
    assert filtered["summary"]["count"] == 3
    assert filtered["summary"]["value"] == Decimal("47476")
    assert filtered["stages"][3]["count"] == 0
    assert get_load_board(search="vehicle-1")["summary"]["count"] == 4
    assert get_load_board(search="packed")["summary"]["count"] == 1
    assert get_load_board(ship_from=date(2026, 11, 1))["summary"]["count"] == 0


def test_unknown_status_missing_data_and_overcapacity_are_visible():
    sync([
        baq_record("UNKNOWN", "On hold", UD110_Date02=None, UD110_Number05=None,
                   UD110_Number06=None, Calculated_OrderQty=None, Calculated_OrderVal=None),
        baq_record("OVER", "Packed", UD110_Number06=-10),
        baq_record("SHIPPED", "Shipped", UD110_Date02="2026-01-01"),
        baq_record("EARLY", "Scheduled", UD110_Date02="2026-10-01"),
    ])
    board = get_load_board(today=date(2026, 10, 2))
    assert board["unknown_count"] == 1
    assert board["undated_count"] == 1
    assert board["capacity_issue_count"] == 1
    assert board["summary"]["missing_value"] == 1
    assert board["summary"]["missing_quantity"] == 1
    assert board["overdue_count"] == 1  # Shipped loads are never overdue.
    assert board["stages"][-1]["loads"][0]["status"] == "ON HOLD"
    assert board["stages"][-1]["loads"][0]["used_pct"] is None
    packed = board["stages"][2]["loads"][0]
    assert packed["used_pct"] == 110
    assert packed["bar_pct"] == 100  # Only the visual width is clamped.
    assert sum(sum(dataset["data"]) for dataset in board["timeline"]["datasets"]) == 3


def test_successful_sync_replaces_snapshot_and_deduplicates():
    sync([baq_record("OLD")])
    record = baq_record("NEW")
    batch = sync([record, record.copy()])
    assert TransportLoad.query.count() == 1
    assert TransportLoad.query.one().load_id == "NEW"
    assert batch.row_count == 2
    assert batch.rows_inserted == 1
    assert "duplicates" in batch.notes


@pytest.mark.parametrize("overrides", [
    {"UD110_Key1": ""},
    {"UD110_Character03": ""},
    {"UD110_Date02": "not-a-date"},
    {"UD110_Number06": "not-a-number"},
    {"Calculated_OrderVal": "NaN"},
    {"Calculated_OrderQty": "Infinity"},
])
def test_invalid_sync_keeps_last_successful_snapshot(overrides):
    sync([baq_record("OLD")])
    with pytest.raises(ValueError):
        sync([baq_record("NEW", **overrides)])
    assert TransportLoad.query.one().load_id == "OLD"
    board = get_load_board()
    assert board["latest_sync"].status == ImportBatch.STATUS_FAILED
    assert board["last_success"].status == ImportBatch.STATUS_SUCCESS


def test_conflicting_duplicates_and_empty_response_keep_snapshot():
    sync([baq_record("OLD")])
    with pytest.raises(ValueError, match="conflicting"):
        sync([baq_record("NEW"), baq_record("NEW", Calculated_OrderQty=9)])
    with pytest.raises(RuntimeError, match="0 records"):
        sync([])
    assert TransportLoad.query.one().load_id == "OLD"


def test_load_board_renders_real_semantics_and_escapes_data(client, admin_user):
    sync([baq_record("LOAFNUN081026", "Packed", UD110_Character02="<script>alert(1)</script>")])
    login_admin(client)
    response = client.get("/transport/loads")
    assert response.status_code == 200
    html = response.data.decode()
    assert "LOAFNUN081026" in html
    assert "95.5%" in html
    assert "4.5 / 100 SEATS free" in html
    assert "21,546" in html
    assert 'class="transport-truck load-card-truck"' in html
    assert "95.5% allocated" in html
    assert "95.5 percent of capacity allocated; 4.5 SEATS free" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "Read-only Epicor snapshot" in html
    assert "31 Oct 2026 at 00:00:00" not in html
    assert "30 Oct 2026 at 08:30:00" in html
    assert 'id="loadTimelineData"' in html
    assert client.get("/transport/loads?ship_from=invalid").status_code == 400
    assert client.get("/transport/loads?ship_from=2026-11-01&ship_to=2026-10-01").status_code == 400


def test_empty_and_failed_sync_states(client, admin_user):
    login_admin(client)
    assert b"No successful sync yet" in client.get("/transport/loads").data
    sync([baq_record("RETAINED")])
    with pytest.raises(ValueError):
        sync([baq_record("INVALID", UD110_Number05="bad")])
    response = client.get("/transport/loads")
    assert b"The latest Epicor sync failed" in response.data
    assert b"RETAINED" in response.data
    assert b"No dated loads to display" in client.get("/transport/loads?q=no-match").data


def test_overall_summary_has_labelled_totals_and_respects_filters(client, admin_user):
    sync([
        baq_record("ONE", Calculated_OrderQty=3, Calculated_OrderVal=100),
        baq_record("TWO", "Packed", Calculated_OrderQty=5, Calculated_OrderVal=200),
    ])
    login_admin(client)
    html = client.get("/transport/loads").data.decode()
    overall = html.split('aria-labelledby="load-overall-heading">')[1].split(
        '<div class="load-summary-grid"', 1,
    )[0]
    assert ">All loads</h3>" in overall
    assert "<dt>Total loads</dt>" in overall
    assert "<dd>2</dd>" in overall
    assert "<dt>Total value</dt>" in overall
    assert "<dd>£300</dd>" in overall
    assert "<dt>Total units</dt>" in overall
    assert "<dd>8</dd>" in overall
    assert "Production readiness" in overall
    assert 'class="load-summary-card-link fw-semibold text-decoration-none"' in html
    assert 'href="#stage-PACKED"' in html
    filtered = client.get("/transport/loads?q=ONE").data.decode()
    overall = filtered.split('aria-labelledby="load-overall-heading">')[1].split(
        '<div class="load-summary-grid"', 1,
    )[0]
    assert ">Filtered loads</h3>" in overall
    assert "<dd>1</dd>" in overall
    assert "<dd>£100</dd>" in overall
    assert "<dd>3</dd>" in overall


def test_load_board_requires_transport_permission(client, db):
    assert client.get("/transport/loads").status_code == 302
    user = User(username="no_access", email="no_access@test.com", is_active=True)
    user.set_password("NoAccess!Pass1234")
    db.session.add(user)
    db.session.commit()
    client.post("/auth/login", data={"login": user.email, "password": "NoAccess!Pass1234"})
    assert client.get("/transport/loads").status_code == 403


def test_board_sorts_ship_dates_before_undated_loads():
    sync([
        baq_record("LATE", UD110_Date02="2026-12-01"),
        baq_record("UNDATED", UD110_Date02=None),
        baq_record("EARLY", UD110_Date02="2026-10-01"),
    ])
    loads = get_load_board()["stages"][1]["loads"]
    assert [load["load_id"] for load in loads] == ["EARLY", "LATE", "UNDATED"]
