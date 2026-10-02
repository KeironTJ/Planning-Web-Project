"""Tests for transport report filtering."""

from datetime import date, timedelta

import pytest

from app.sales.orders.models import SalesOrder


class TestLoadingBayReport:
    """The report includes completed work and unfinished staged work."""

    @pytest.fixture(autouse=True)
    def ctx(self, app):
        with app.app_context():
            yield

    def test_shipping_dates_use_current_ship_by_not_need_by_or_original(self, db):
        from app.transport.services import get_loading_bay_report

        today = date(2026, 10, 7)
        dates = [
            (1, today - timedelta(days=1), today + timedelta(days=20), 10),
            (2, today, today - timedelta(days=20), 20),
            (3, today + timedelta(days=2), today - timedelta(days=20), 30),
            (4, None, today - timedelta(days=20), 40),
            (5, today + timedelta(days=35), today - timedelta(days=20), 50),
        ]
        db.session.add_all([
            SalesOrder(
                order_num=order, order_line=1, rel_num=1, open_order=True, assembly_seq=0,
                wip_bin="BAY-01", required_qty=1, qty_completed=1, selling_qty=1,
                release_price_gbp=value, req_date=ship_by, need_by_date=need_by,
                original_ship_by=today - timedelta(days=50), order_held=order == 3,
            )
            for order, ship_by, need_by, value in dates
        ])
        db.session.commit()
        report = get_loading_bay_report(today=today)
        orders = {order["order_num"]: order for order in report["orders"]}
        assert orders[1]["ship_by_date"] == today - timedelta(days=1)
        assert orders[1]["days_delta"] == -1
        assert orders[2]["days_delta"] == 0
        assert orders[3]["days_delta"] == 2
        assert orders[4]["ship_by_date"] is None
        assert orders[4]["days_delta"] is None
        assert orders[1]["releases"][0]["ship_by_date"] == today - timedelta(days=1)
        assert report["shipping_horizon"]["ready"] == [10, 20, 0, 0, 0, 50, 40]
        assert report["shipping_horizon"]["ready_hold"] == [0, 0, 30, 0, 0, 0, 0]
        assert report["weekly_loading"]["ready"] == [10, 20, 0, 0, 0, 0, 50, 40]
        assert report["weekly_loading"]["ready_hold"] == [0, 30, 0, 0, 0, 0, 0, 0]
        assert report["sort"] == "ship_by_date"
        legacy = get_loading_bay_report(sort="due_date", today=today)
        assert [order["order_num"] for order in legacy["orders"]] == [1, 2, 5, 4, 3]

    def test_ship_by_uses_earliest_release_and_labels_both_pages(self, client, db, admin_user):
        from app.transport.services import get_loading_bay_report

        today = date.today()
        db.session.add_all([
            SalesOrder(order_num=101, order_line=index, rel_num=1, open_order=True,
                       assembly_seq=0, wip_bin="BAY-01", required_qty=1, qty_completed=1,
                       selling_qty=1, release_price_gbp=25, req_date=ship_by,
                       need_by_date=today + timedelta(days=100))
            for index, ship_by in enumerate([None, today + timedelta(days=5), today], 1)
        ])
        db.session.commit()
        order = get_loading_bay_report(today=today)["orders"][0]
        assert order["ship_by_date"] == today
        assert order["days_delta"] == 0
        client.post("/auth/login", data={
            "login": "admin@test.com", "password": "Admin!Pass1234",
        })
        html = client.get("/transport/loading-bay?sort=ship_by_date").data.decode()
        assert "Loading Value by Ship-by Week" in html
        assert "<th>Ship By</th>" in html
        assert 'value="ship_by_date" selected' in html
        assert today.strftime("%d %b %Y") in html
        assert "<th>Due Date</th>" not in html
        dashboard = client.get("/transport/dashboard").data.decode()
        assert "Ready-order value by ship-by date" in dashboard
        assert "Ready-order value by required date" not in dashboard
        assert "OrderRel_ReqDate" in dashboard

    def test_bay_state_dates_and_sorting_use_ship_by(self, client, db, admin_user):
        from app.transport.services import get_loading_bay_state

        today = date.today()
        db.session.add_all([
            SalesOrder(order_num=order, order_line=1, rel_num=1, open_order=True,
                       assembly_seq=0, wip_bin="BAY-01", required_qty=1, qty_completed=1,
                       req_date=ship_by, need_by_date=need_by,
                       original_ship_by=today - timedelta(days=100))
            for order, ship_by, need_by in [
                (1, today + timedelta(days=7), today - timedelta(days=7)),
                (2, today - timedelta(days=2), today + timedelta(days=7)),
                (3, None, today - timedelta(days=7)),
                (4, today, today + timedelta(days=7)),
            ]
        ])
        db.session.commit()
        lines = get_loading_bay_state(today=today)["bay_board"][0]["lines"]
        assert [line["order_num"] for line in lines] == [2, 4, 1, 3]
        assert [line["days_delta"] for line in lines] == [-2, 0, 7, None]
        assert lines[3]["ship_by_date"] is None
        client.post("/auth/login", data={
            "login": "admin@test.com", "password": "Admin!Pass1234",
        })
        html = client.get("/transport/bay-state").data.decode()
        assert "2d overdue" in html
        assert "Ship by today" in html
        assert "Ship by " + (today + timedelta(days=7)).strftime("%d %b %Y") in html
        assert "Ship by: Not set" in html
        assert "Due today" not in html

    def test_includes_only_bay_assigned_orders(self, client, db, admin_user):
        db.session.add_all([
            SalesOrder(
                order_num=10001,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Staged unfinished customer",
                wip_bin="BAY-01",
                required_qty=1,
                qty_completed=0,
                need_by_date=date.today(),
            ),
            SalesOrder(
                order_num=10002,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Unassigned finished customer",
                required_qty=1,
                qty_completed=1,
                need_by_date=date.today(),
            ),
            SalesOrder(
                order_num=10003,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Whitespace location customer",
                wip_bin="   ",
                required_qty=1,
                qty_completed=1,
                need_by_date=date.today(),
            ),
            SalesOrder(
                order_num=10004,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Unassigned unfinished customer",
                required_qty=1,
                qty_completed=0,
                need_by_date=date.today(),
            ),
        ])
        db.session.commit()

        client.post("/auth/login", data={
            "login": "admin@test.com",
            "password": "Admin!Pass1234",
            "remember": False,
        }, follow_redirects=True)
        response = client.get("/transport/loading-bay")

        assert response.status_code == 200
        assert b"Staged unfinished customer" in response.data
        assert b"Unassigned finished customer" not in response.data
        assert b"Whitespace location customer" not in response.data
        assert b"Unassigned unfinished customer" not in response.data

    def test_excludes_void_lines(self, client, db, admin_user):
        db.session.add_all([
            SalesOrder(
                order_num=10010,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Voided line customer",
                wip_bin="BAY-06",
                void_line=True,
                required_qty=1,
                qty_completed=0,
                need_by_date=date.today(),
            ),
        ])
        db.session.commit()

        client.post("/auth/login", data={
            "login": "admin@test.com",
            "password": "Admin!Pass1234",
            "remember": False,
        }, follow_redirects=True)
        response = client.get("/transport/loading-bay")

        assert response.status_code == 200
        assert b"Voided line customer" not in response.data

    def test_flags_shipped_and_partially_shipped_lines(self, client, db, admin_user):
        db.session.add_all([
            SalesOrder(
                order_num=10005,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Fully shipped customer",
                wip_bin="BAY-01",
                selling_qty=10,
                shipped_qty=10,
                required_qty=10,
                qty_completed=10,
                need_by_date=date.today(),
            ),
            SalesOrder(
                order_num=10006,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Partially shipped customer",
                wip_bin="BAY-02",
                selling_qty=10,
                shipped_qty=4,
                required_qty=10,
                qty_completed=10,
                need_by_date=date.today(),
            ),
            SalesOrder(
                order_num=10007,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Unshipped customer",
                wip_bin="BAY-03",
                selling_qty=10,
                shipped_qty=0,
                required_qty=10,
                qty_completed=10,
                need_by_date=date.today(),
            ),
        ])
        db.session.commit()

        client.post("/auth/login", data={
            "login": "admin@test.com",
            "password": "Admin!Pass1234",
            "remember": False,
        }, follow_redirects=True)
        response = client.get("/transport/loading-bay")

        assert response.status_code == 200
        html = response.data.decode()
        assert 'title="Shipped 10 / 10"' in html
        assert "Shipped 4/10" in html
        # Fully shipped line (order 10005) should show only the "Shipped"
        # badge, not also "Finished" — only the other two orders (which
        # are build-complete but not fully shipped) should show it.
        assert html.count('bi-check2"></i> Finished') == 2

    def test_shows_next_operation_instead_of_generic_wip_badge(self, client, db, admin_user):
        from app.operations.models import WorksOrder

        db.session.add_all([
            SalesOrder(
                order_num=10008,
                order_line=1,
                rel_num=1,
                job_num="JOB-001",
                open_order=True,
                assembly_seq=0,
                customer_name="Not started customer",
                wip_bin="BAY-04",
                selling_qty=10,
                shipped_qty=0,
                required_qty=10,
                qty_completed=0,
                need_by_date=date.today(),
            ),
            WorksOrder(
                job_num="JOB-001",
                assembly_seq=0,
                job_complete=False,
                next_op="Cutting",
            ),
        ])
        db.session.commit()

        client.post("/auth/login", data={
            "login": "admin@test.com",
            "password": "Admin!Pass1234",
            "remember": False,
        }, follow_redirects=True)
        response = client.get("/transport/loading-bay")

        assert response.status_code == 200
        html = response.data.decode()
        assert "Cutting" in html
        assert 'bi-clock"></i> WIP' not in html

    def test_completed_staged_release_is_ready_when_job_flag_is_stale(self, client, db, admin_user):
        from app.operations.models import WorksOrder

        db.session.add_all([
            SalesOrder(
                order_num=10011,
                order_line=1,
                rel_num=1,
                job_num="JOB-002",
                open_order=True,
                assembly_seq=0,
                customer_name="Completed staged customer",
                wip_bin="BAY-07",
                selling_qty=10,
                shipped_qty=0,
                required_qty=10,
                qty_completed=10,
                need_by_date=date.today(),
            ),
            WorksOrder(
                job_num="JOB-002",
                assembly_seq=0,
                job_complete=False,
                next_op="Packing",
            ),
        ])
        db.session.commit()

        client.post("/auth/login", data={
            "login": "admin@test.com",
            "password": "Admin!Pass1234",
            "remember": False,
        }, follow_redirects=True)
        response = client.get("/transport/loading-bay")

        assert response.status_code == 200
        html = response.data.decode()
        assert "Completed staged customer" in html
        assert "Ready to Ship" in html
        from app.transport.services import get_loading_bay_report

        report = get_loading_bay_report()
        assert report["orders"][0]["order_status"] == "ready"

    def test_customer_credit_hold_does_not_put_order_on_hold(self, db):
        db.session.add_all([
            SalesOrder(
                order_num=10012,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Customer hold only",
                customer_credit_hold=True,
                wip_bin="BAY-08",
                selling_qty=1,
                required_qty=1,
                qty_completed=1,
                need_by_date=date.today(),
            ),
            SalesOrder(
                order_num=10013,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="SO credit hold",
                so_credit_hold=True,
                wip_bin="BAY-09",
                selling_qty=1,
                required_qty=1,
                qty_completed=1,
                need_by_date=date.today(),
            ),
            SalesOrder(
                order_num=10014,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Order hold",
                order_held=True,
                wip_bin="BAY-10",
                selling_qty=1,
                required_qty=1,
                qty_completed=1,
                need_by_date=date.today(),
            ),
        ])
        db.session.commit()

        from app.transport.services import get_loading_bay_report, get_loading_bay_state

        report_holds = {
            order["order_num"]: order["on_hold"]
            for order in get_loading_bay_report()["orders"]
        }
        state_holds = {
            line["order_num"]: line["on_hold"]
            for bay in get_loading_bay_state()["bay_board"]
            for line in bay["lines"]
        }

        assert report_holds == {10012: False, 10013: True, 10014: True}
        assert state_holds == {10012: False, 10013: True, 10014: True}

    def test_invoiceable_column_excludes_shipped_value_from_potential_total(self, client, db, admin_user):
        db.session.add_all([
            SalesOrder(
                order_num=10009,
                order_line=1,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Mixed shipped customer",
                wip_bin="BAY-05",
                selling_qty=10,
                shipped_qty=10,
                required_qty=10,
                qty_completed=10,
                release_price_gbp=150,
                need_by_date=date.today(),
            ),
            SalesOrder(
                order_num=10009,
                order_line=2,
                rel_num=1,
                open_order=True,
                assembly_seq=0,
                customer_name="Mixed shipped customer",
                wip_bin="BAY-05",
                selling_qty=5,
                shipped_qty=0,
                required_qty=5,
                qty_completed=5,
                release_price_gbp=50,
                need_by_date=date.today(),
            ),
        ])
        db.session.commit()

        client.post("/auth/login", data={
            "login": "admin@test.com",
            "password": "Admin!Pass1234",
            "remember": False,
        }, follow_redirects=True)
        response = client.get("/transport/loading-bay")

        assert response.status_code == 200
        html = response.data.decode()
        # Invoiceable (current) value includes both bay-assigned lines;
        # potential total excludes the already-shipped line's value.
        assert "£200.00" in html
        assert "/ £50.00" in html
