"""
Tests for orders service layer -- comment validation.
"""

import pytest

from app.sales.orders.models import SalesOrderComment
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
