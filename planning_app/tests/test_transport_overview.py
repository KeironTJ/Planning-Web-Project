"""Management dashboard reconciles with its source reports, without combining them."""

from datetime import date
from decimal import Decimal

import pytest

from app.auth.models import User
from app.sales.orders.models import SalesOrder
from app.transport.load_board import get_load_board
from app.transport.overview import build_overview, build_management_summary
from app.transport.services import get_loading_bay_report, get_loading_bay_state
from tests.test_transport_loads import baq_record, sync, login_admin
from tests.test_transport_readiness import record, sync_orders


@pytest.fixture(autouse=True)
def ctx(app):
    with app.app_context():
        yield


def test_visuals_reconcile_values_dates_and_capacity_without_shipped_loads():
    sync([
        baq_record("PLANNED", "Planned", Calculated_OrderVal=100, UD110_Date02="2026-10-01"),
        baq_record("PACKED", "Packed", Calculated_OrderVal=300, UD110_Date02="2026-10-01"),
        baq_record("SHIPPED", "Shipped", Calculated_OrderVal=200, UD110_Date02="2026-10-02"),
        baq_record("UNDATED", Calculated_OrderVal=None, UD110_Date02=None,
                   UD110_Number05=None, UD110_Number06=None),
        baq_record("OTHER", "On hold", Calculated_OrderVal=-50, UD110_Date02=None),
    ])
    board = get_load_board(today=date(2026, 10, 2))
    overview = build_overview(board)
    assert overview["active_value"] == Decimal(350)
    assert overview["active_missing_value"] == 1
    assert overview["undated_value"] == -50
    assert overview["negative_values"]
    assert overview["capacity_known"] == 3
    assert [load["load_id"] for load in overview["active_loads"]] == [
        "PACKED", "PLANNED", "OTHER", "UNDATED",
    ]
    assert [day["value"] for day in overview["schedule"]] == [400, 200]
    assert sum(day["value"] for day in overview["schedule"]) + overview["undated_value"] == board["summary"]["value"]
    assert [segment["width"] for segment in overview["schedule"][0]["segments"]] == [25, 0, 75, 0, 0]
    assert overview["schedule"][1]["segments"][3]["width"] == 50


def test_shipped_hold_not_counted_as_unshipped_exception():
    sync([baq_record("ACTIVE"), baq_record("SHIPPED", "Shipped")])
    sync_orders([
        record(load="ACTIVE", OrderHed_OrderHeld=True),
        record(2, load="SHIPPED", OrderHed_OrderHeld=True),
    ])
    assert build_overview(get_load_board())["held_loads"] == 1


def test_empty_and_negative_chart_scales_are_explicit():
    empty = build_overview(get_load_board())
    assert empty["schedule"] == []
    assert empty["active_value"] == 0
    sync([baq_record(Calculated_OrderVal=-50)])
    visual = build_overview(get_load_board())
    assert visual["negative_values"]
    assert visual["schedule"][0]["value"] == -50
    assert visual["schedule"][0]["segments"][1]["width"] == 100
    management = build_management_summary(
        {"shipping_horizon": {"labels": ["Today"], "ready": [-20], "ready_hold": [30]}},
        {"bay_board": []},
    )
    assert management["negative_values"]
    assert management["horizons"][0]["ready_width"] == 40
    assert management["horizons"][0]["held_width"] == 60


def test_dashboard_combines_reports_and_scopes_filters_to_loads(client, admin_user, db):
    sync([baq_record("ONE", Calculated_OrderVal=100), baq_record("TWO", Calculated_OrderVal=200)])
    db.session.add_all([
        SalesOrder(order_num=100, order_line=1, rel_num=1, open_order=True,
                   assembly_seq=0, wip_bin="<BAY-01>", required_qty=2, qty_completed=2,
                   selling_qty=2, release_price_gbp=80, need_by_date=date.today(), req_date=date.today()),
        SalesOrder(order_num=200, order_line=1, rel_num=1, open_order=True,
                   assembly_seq=0, wip_bin="BAY-02", required_qty=1, qty_completed=1,
                   selling_qty=1, release_price_gbp=40, order_held=True,
                   need_by_date=date.today(), req_date=date.today()),
    ])
    db.session.commit()
    shipping = get_loading_bay_report()
    bay = get_loading_bay_state()
    management = build_management_summary(shipping, bay)
    assert sum(row["ready"] for row in management["horizons"]) == shipping["kpi_summary"]["ready_value"] == 80
    assert sum(row["held"] for row in management["horizons"]) == 40
    assert sum(row["qty"] for row in management["bays"]) == bay["total_qty"] == 3
    assert management["bays"][0]["width"] == 100
    login_admin(client)
    for path in ("/transport/", "/transport/dashboard"):
        response = client.get(path + "?q=ONE")
        assert response.status_code == 200
        html = response.data.decode()
        assert "Transport Dashboard" in html
        assert "Shipping value &amp; loading bay" in html
        assert "&lt;BAY-01&gt;" in html
        assert "<BAY-01>" not in html
        assert "£80" in html and "£40" in html
        assert "Independent of the load filters below" in html
        assert "Filtered loads" in html
        assert "ONE" in html and "TWO" not in html
        assert "Print / save PDF" in html
        assert "Order value on shipped loads" in html
        assert "not invoiced revenue" in html
        assert "not packing or physical loading" in html
    redirect = client.get("/transport/overview?q=ONE")
    assert redirect.status_code == 302
    assert redirect.location.endswith("/transport/dashboard?q=ONE")


def test_dashboard_empty_missing_and_error_states(client, admin_user):
    login_admin(client)
    html = client.get("/transport/dashboard").data.decode()
    assert "No load snapshot available" in html
    assert "No dated loads match this view" in html
    assert "No successful Epicor sync recorded" in html
    assert "No finished-goods lines" in html
    assert "Order details not synced" in html
    assert client.get("/transport/dashboard?ship_from=bad").status_code == 400
    assert client.get("/transport/dashboard?ship_from=2026-12-01&ship_to=2026-11-01").status_code == 400
    sync([baq_record(Calculated_OrderVal=None, Calculated_OrderQty=None,
                     UD110_Number06=-10, UD110_Date02=None)])
    html = client.get("/transport/dashboard").data.decode()
    assert "1 values missing" in html
    assert "1 quantities missing" in html
    assert "undated loads" in html
    assert "110% allocated" in html
    assert "width: 100%" in html
    assert "Check capacity figures" in html
    with pytest.raises(ValueError):
        sync([baq_record(UD110_Date02="bad")])
    assert b"Load sync failed" in client.get("/transport/dashboard").data


def test_many_bay_locations_remain_available_in_keyboard_scroll_region(client, admin_user, db):
    db.session.add_all([
        SalesOrder(order_num=1000 + index, order_line=1, rel_num=1,
                   open_order=True, assembly_seq=0, wip_bin=f"BAY-{index:02}",
                   required_qty=1, qty_completed=1, selling_qty=1, release_price_gbp=10)
        for index in range(30)
    ])
    db.session.commit()
    login_admin(client)
    html = client.get("/transport/dashboard").data.decode()
    assert "30 locations" in html
    region = html.split(
        'class="overview-bay-list" role="region" aria-labelledby="overview-bays" tabindex="0">',
    )[1].split("</section>", 1)[0]
    assert region.count('class="overview-bay-row"') == 30
    assert "BAY-00" in region and "BAY-29" in region


def test_dashboard_requires_transport_permission(client, db):
    assert client.get("/transport/dashboard").status_code == 302
    user = User(username="no_transport", email="no_transport@test.com", is_active=True)
    user.set_password("NoTransport!Pass1234")
    db.session.add(user)
    db.session.commit()
    client.post("/auth/login", data={"login": user.email, "password": "NoTransport!Pass1234"})
    for path in ("/transport/", "/transport/dashboard", "/transport/overview"):
        assert client.get(path).status_code == 403
