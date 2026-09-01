"""
Tests for orders service layer -- comment validation.
"""

from datetime import date, timedelta

import pytest

from app.sales.orders.models import SalesOrder, SalesOrderComment
from app.core.exceptions import ValidationError
from app.extensions import db as _db


class TestSoCommentValidation:
    """add_so_comment validates body before inserting."""

    @pytest.fixture(autouse=True)
    def ctx(self, app):
        with app.app_context():
            yield

    def test_blank_body_raises_validation_error(self, db):
        from app.sales.orders.services import add_so_comment
        with pytest.raises(ValidationError, match="blank"):
            add_so_comment("10001", user_id=None, body="")

    def test_whitespace_only_body_raises_validation_error(self, db):
        from app.sales.orders.services import add_so_comment
        with pytest.raises(ValidationError, match="blank"):
            add_so_comment("10002", user_id=None, body="   ")

    def test_valid_body_persists(self, db, admin_user):
        from app.sales.orders.services import add_so_comment
        comment = add_so_comment("10003", user_id=admin_user.id, body="Test note")
        assert comment.id is not None
        assert comment.so_number == "10003"
        assert comment.body == "Test note"

    def test_body_is_stripped_before_save(self, db, admin_user):
        from app.sales.orders.services import add_so_comment
        comment = add_so_comment("10004", user_id=admin_user.id, body="  note  ")
        assert comment.body == "note"

    def test_comment_does_not_require_so_in_db(self, db, admin_user):
        # SO number is a free-form string; no FK constraint to a SO table
        from app.sales.orders.services import add_so_comment
        comment = add_so_comment("DOES-NOT-EXIST", user_id=admin_user.id, body="Orphan note")
        assert comment.id is not None


class TestOrderBookStatus:
    """The shared order-book query keeps open and closed orders separate."""

    @pytest.fixture(autouse=True)
    def ctx(self, app):
        with app.app_context():
            yield

    @pytest.fixture
    def orders(self, db):
        db.session.add_all([
            SalesOrder(
                order_num=10001,
                order_line=1,
                rel_num=1,
                open_order=True,
                customer_name="Open Customer",
                model="Chair",
                selling_qty=2,
                release_price_gbp=500,
                need_by_date=date.today() - timedelta(days=1),
            ),
            SalesOrder(
                order_num=10002,
                order_line=1,
                rel_num=1,
                open_order=False,
                customer_name="Closed Customer",
                model="Sofa",
                selling_qty=3,
                release_price_gbp=900,
                need_by_date=date.today() - timedelta(days=30),
            ),
        ])
        db.session.commit()

    def test_get_order_book_switches_between_open_and_closed(self, orders):
        from app.sales.orders.services import get_order_book

        open_pagination, open_orders = get_order_book(order_status="open")
        closed_pagination, closed_orders = get_order_book(order_status="closed")

        assert open_pagination.total == 1
        assert [order["so_number"] for order in open_orders] == ["10001"]
        assert closed_pagination.total == 1
        assert [order["so_number"] for order in closed_orders] == ["10002"]

    def test_closed_summary_uses_closed_order_aggregates(self, orders):
        from app.sales.orders.services import get_order_book_summary

        summary = get_order_book_summary("closed")

        assert summary["total"] == 1
        assert summary["total_units"] == 3
        assert summary["total_value"] == 900
        assert summary["average_value"] == 900
