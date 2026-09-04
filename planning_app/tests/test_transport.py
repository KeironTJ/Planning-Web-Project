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

    def test_includes_finished_or_bay_assigned_orders(self, client, db, admin_user):
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
        assert b"Unassigned finished customer" in response.data
        assert b"Whitespace location customer" in response.data
        assert b"Unassigned unfinished customer" not in response.data
