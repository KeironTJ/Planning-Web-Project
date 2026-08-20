"""
Tests for capacity planning service layer.

Covers: CapacityBucket available hours, override_bucket, get_capacity_dashboard.
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal

from app.planning.capacity.models import CapacityBucket
from app.planning.capacity.services import (
    get_available_by_week_dept,
    get_capacity_dashboard,
    override_bucket,
    _week_label,
    _week_start,
)
from app.sales.orders.models import Department
from app.extensions import db as _db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MONDAY = _week_start(date.today())


def make_dept(name="Assembly", code="ASSY"):
    dept = Department(name=name, code=code, is_active=True)
    _db.session.add(dept)
    _db.session.flush()  # get id before commit
    return dept


def make_bucket(dept_id, day, hours=8.0, is_workday=True):
    bucket = CapacityBucket(
        department_id=dept_id,
        date=day,
        week=_week_label(day),
        available_hours=Decimal(str(hours)),
        is_workday=is_workday,
    )
    _db.session.add(bucket)
    return bucket


# ---------------------------------------------------------------------------
# Unit tests: date helpers -- pure functions
# ---------------------------------------------------------------------------

class TestDateHelpers:

    def test_week_start_returns_monday(self):
        monday = date(2026, 8, 17)   # known Monday
        assert _week_start(monday) == monday

    def test_week_start_of_wednesday_is_monday(self):
        wednesday = date(2026, 8, 19)
        assert _week_start(wednesday) == date(2026, 8, 17)

    def test_week_label_format(self):
        monday = date(2026, 8, 17)
        assert _week_label(monday) == "2026-W34"

    def test_week_start_of_sunday_is_prev_monday(self):
        sunday = date(2026, 8, 16)
        assert _week_start(sunday) == date(2026, 8, 10)


# ---------------------------------------------------------------------------
# Integration tests: CapacityBucket queries
# ---------------------------------------------------------------------------

class TestAvailableByWeekDept:

    @pytest.fixture(autouse=True)
    def ctx(self, app):
        with app.app_context():
            yield

    def test_returns_hours_for_dept_in_week(self, db):
        dept = make_dept("Cutting", "CUT")
        make_bucket(dept.id, MONDAY,     hours=8.0)
        make_bucket(dept.id, MONDAY + timedelta(days=1), hours=7.5)
        _db.session.commit()
        result = get_available_by_week_dept(MONDAY, MONDAY + timedelta(days=6))
        week = _week_label(MONDAY)
        assert result[(week, dept.id)] == pytest.approx(15.5)

    def test_non_workdays_excluded(self, db):
        dept = make_dept("Sewing", "SEW")
        make_bucket(dept.id, MONDAY, hours=8.0, is_workday=True)
        make_bucket(dept.id, MONDAY + timedelta(days=5), hours=8.0, is_workday=False)
        _db.session.commit()
        result = get_available_by_week_dept(MONDAY, MONDAY + timedelta(days=6))
        week = _week_label(MONDAY)
        assert result[(week, dept.id)] == pytest.approx(8.0)

    def test_floor_date_excludes_past_days(self, db):
        dept = make_dept("Frame", "FRM")
        make_bucket(dept.id, MONDAY,     hours=8.0)
        make_bucket(dept.id, MONDAY + timedelta(days=1), hours=8.0)
        _db.session.commit()
        # Floor to Tuesday -- Monday's hours should be excluded
        result = get_available_by_week_dept(
            MONDAY, MONDAY + timedelta(days=6),
            floor_date=MONDAY + timedelta(days=1),
        )
        week = _week_label(MONDAY)
        assert result[(week, dept.id)] == pytest.approx(8.0)

    def test_different_departments_independent(self, db):
        dept_a = make_dept("Dept A", "DA")
        dept_b = make_dept("Dept B", "DB")
        make_bucket(dept_a.id, MONDAY, hours=10.0)
        make_bucket(dept_b.id, MONDAY, hours=6.0)
        _db.session.commit()
        result = get_available_by_week_dept(MONDAY, MONDAY + timedelta(days=6))
        week = _week_label(MONDAY)
        assert result[(week, dept_a.id)] == pytest.approx(10.0)
        assert result[(week, dept_b.id)] == pytest.approx(6.0)

    def test_out_of_range_dates_not_included(self, db):
        dept = make_dept("Dispatch", "DISP")
        next_week_monday = MONDAY + timedelta(weeks=1)
        make_bucket(dept.id, next_week_monday, hours=8.0)
        _db.session.commit()
        result = get_available_by_week_dept(MONDAY, MONDAY + timedelta(days=6))
        week = _week_label(MONDAY)
        assert (week, dept.id) not in result


# ---------------------------------------------------------------------------
# Integration tests: override_bucket
# ---------------------------------------------------------------------------

class TestOverrideBucket:

    @pytest.fixture(autouse=True)
    def ctx(self, app):
        with app.app_context():
            yield

    def test_override_sets_hours_and_flag(self, db):
        dept = make_dept("Paint", "PNT")
        bucket = make_bucket(dept.id, MONDAY, hours=8.0)
        _db.session.commit()
        updated = override_bucket(bucket.id, 6.5)
        assert float(updated.available_hours) == pytest.approx(6.5)
        assert updated.manually_overridden is True

    def test_override_nonexistent_raises(self, db):
        from app.core.exceptions import NotFoundError
        with pytest.raises(NotFoundError):
            override_bucket(99999, 8.0)

    def test_override_to_zero_allowed(self, db):
        dept = make_dept("QC", "QC")
        bucket = make_bucket(dept.id, MONDAY, hours=8.0)
        _db.session.commit()
        updated = override_bucket(bucket.id, 0.0)
        assert float(updated.available_hours) == 0.0
        assert updated.manually_overridden is True


# ---------------------------------------------------------------------------
# Integration tests: get_capacity_dashboard
# ---------------------------------------------------------------------------

class TestCapacityDashboard:

    @pytest.fixture(autouse=True)
    def ctx(self, app):
        with app.app_context():
            yield

    def test_returns_expected_keys(self, db):
        result = get_capacity_dashboard(from_date=MONDAY, num_weeks=4)
        assert all(k in result for k in ("weeks", "departments", "summary", "has_buckets", "from_date", "to_date"))

    def test_has_buckets_false_with_no_data(self, db):
        result = get_capacity_dashboard(from_date=MONDAY, num_weeks=4)
        assert result["has_buckets"] is False

    def test_has_buckets_true_when_data_present(self, db):
        dept = make_dept("Weld", "WLD")
        make_bucket(dept.id, MONDAY, hours=8.0)
        _db.session.commit()
        result = get_capacity_dashboard(from_date=MONDAY, num_weeks=4)
        assert result["has_buckets"] is True

    def test_week_count_matches_num_weeks(self, db):
        result = get_capacity_dashboard(from_date=MONDAY, num_weeks=6)
        assert len(result["weeks"]) == 6

    def test_dept_filter_scopes_to_one_dept(self, db):
        dept_a = make_dept("Target", "TGT")
        make_dept("Other", "OTH")
        _db.session.commit()
        result = get_capacity_dashboard(from_date=MONDAY, num_weeks=4, dept_id=dept_a.id)
        assert len(result["departments"]) == 1
        assert result["departments"][0]["dept"].id == dept_a.id

    def test_avail_hours_aggregated_correctly(self, db):
        dept = make_dept("Pack", "PCK")
        make_bucket(dept.id, MONDAY, hours=8.0)
        make_bucket(dept.id, MONDAY + timedelta(days=1), hours=6.0)
        _db.session.commit()
        result = get_capacity_dashboard(from_date=MONDAY, num_weeks=1)
        dept_entry = next(d for d in result["departments"] if d["dept"].id == dept.id)
        week_row = dept_entry["rows"][0]
        assert week_row["avail"] == pytest.approx(14.0)
