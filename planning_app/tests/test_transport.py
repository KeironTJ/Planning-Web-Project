"""Tests for transport report filtering."""

from datetime import date

import pytest

from app.sales.orders.models import SalesOrder


class TestLoadingBayReport:
    """The report includes completed work and unfinished staged work."""

    @pytest.fixture(autouse=True)
    def ctx(self, app):
        with app.app_context():
            yield

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
