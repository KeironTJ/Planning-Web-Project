"""
Tests for the capacity planning module.

Covers: WorkCentre CRUD, capacity bucket generation, work order lifecycle.

TODO: These tests were written against an older capacity API (WorkCentre/WorkOrder
models) that has since been replaced by CapacityBucket + Department.
Skipped until rewritten against the current services.
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal

pytestmark = pytest.mark.skip(reason="Tests reference removed WorkCentre/WorkOrder API — needs rewrite")

from app.planning.capacity.models import CapacityBucket
from app.planning.capacity.services import get_capacity_dashboard
from app.core.exceptions import NotFoundError, ValidationError
from .conftest import login


class TestWorkCentreService:
    def test_create_work_centre(self, db_session):
        wc = WorkCentreService.create_work_centre({
            "code": "TEST01",
            "name": "Test Machine",
            "department": "Production",
            "hours_per_shift": Decimal("8.0"),
            "shifts_per_day": 1,
            "efficiency_pct": Decimal("85.0"),
        })
        assert wc.id is not None
        assert wc.code == "TEST01"

    def test_create_duplicate_code_raises(self, db_session):
        WorkCentreService.create_work_centre({
            "code": "DUP01",
            "name": "First",
            "hours_per_shift": Decimal("8.0"),
            "shifts_per_day": 1,
            "efficiency_pct": Decimal("85.0"),
        })
        with pytest.raises(ValidationError, match="already exists"):
            WorkCentreService.create_work_centre({
                "code": "DUP01",
                "name": "Second",
                "hours_per_shift": Decimal("8.0"),
                "shifts_per_day": 1,
                "efficiency_pct": Decimal("85.0"),
            })

    def test_get_nonexistent_raises(self, db_session):
        with pytest.raises(NotFoundError):
            WorkCentreService.get_work_centre(99999)

    def test_daily_capacity_calculation(self, db_session):
        wc = WorkCentreService.create_work_centre({
            "code": "CALC01",
            "name": "Calc Test",
            "hours_per_shift": Decimal("8.0"),
            "shifts_per_day": 2,
            "efficiency_pct": Decimal("100.0"),
        })
        # 8h × 2 shifts × 100% = 16h
        assert wc.daily_capacity_hours == Decimal("16.00")


class TestCapacityBuckets:
    def test_generate_weekly_buckets(self, db_session):
        wc = WorkCentreService.create_work_centre({
            "code": "BUCK01",
            "name": "Bucket Test WC",
            "hours_per_shift": Decimal("8.0"),
            "shifts_per_day": 1,
            "efficiency_pct": Decimal("85.0"),
        })
        today = date.today()
        buckets = CapacityService.generate_weekly_buckets(wc.id, from_date=today, weeks=4)
        assert len(buckets) == 4
        # All should start on Monday
        for b in buckets:
            assert b.period_start.weekday() == 0

    def test_generate_buckets_unknown_wc(self, db_session):
        with pytest.raises(NotFoundError):
            CapacityService.generate_weekly_buckets(99999, date.today(), weeks=2)

    def test_allocate_hours_success(self, db_session):
        wc = WorkCentreService.create_work_centre({
            "code": "ALLOC01",
            "name": "Alloc Test",
            "hours_per_shift": Decimal("8.0"),
            "shifts_per_day": 1,
            "efficiency_pct": Decimal("100.0"),
        })
        today = date.today()
        buckets = CapacityService.generate_weekly_buckets(wc.id, today, weeks=1)
        bucket = buckets[0]
        initial_allocated = bucket.allocated_hours
        CapacityService.allocate_hours(bucket.id, Decimal("4.0"))
        db_session.refresh(bucket)
        assert bucket.allocated_hours == Decimal(str(initial_allocated)) + Decimal("4.0")

    def test_allocate_exceeds_capacity_raises(self, db_session):
        wc = WorkCentreService.create_work_centre({
            "code": "OVER01",
            "name": "Over Test",
            "hours_per_shift": Decimal("8.0"),
            "shifts_per_day": 1,
            "efficiency_pct": Decimal("100.0"),
        })
        buckets = CapacityService.generate_weekly_buckets(wc.id, date.today(), weeks=1)
        bucket = buckets[0]
        with pytest.raises(CapacityError, match="exceed available capacity"):
            CapacityService.allocate_hours(bucket.id, Decimal("9999.0"))


class TestWorkOrderService:
    def test_create_work_order(self, db_session, planner_user):
        wo = WorkOrderService.create_work_order({
            "order_number": "WO-TEST-001",
            "product_code": "PROD001",
            "quantity": Decimal("100"),
            "planned_start": date.today(),
            "planned_end": date.today() + timedelta(days=7),
        }, created_by_id=planner_user.id)
        assert wo.id is not None
        assert wo.status == WorkOrder.STATUS_DRAFT
        assert wo.completion_pct == 0.0

    def test_duplicate_order_number_raises(self, db_session, planner_user):
        WorkOrderService.create_work_order({
            "order_number": "WO-DUP-001",
            "product_code": "PROD001",
            "quantity": Decimal("10"),
            "planned_start": date.today(),
            "planned_end": date.today() + timedelta(days=3),
        }, created_by_id=planner_user.id)
        with pytest.raises(ValidationError, match="already exists"):
            WorkOrderService.create_work_order({
                "order_number": "WO-DUP-001",
                "product_code": "PROD002",
                "quantity": Decimal("5"),
                "planned_start": date.today(),
                "planned_end": date.today() + timedelta(days=3),
            }, created_by_id=planner_user.id)

    def test_release_work_order(self, db_session, planner_user):
        wo = WorkOrderService.create_work_order({
            "order_number": "WO-REL-001",
            "product_code": "PROD001",
            "quantity": Decimal("50"),
            "planned_start": date.today(),
            "planned_end": date.today() + timedelta(days=5),
        }, created_by_id=planner_user.id)
        released = WorkOrderService.release_work_order(wo.id)
        assert released.status == WorkOrder.STATUS_RELEASED

    def test_cannot_release_completed_order(self, db_session, planner_user):
        wo = WorkOrderService.create_work_order({
            "order_number": "WO-BAD-001",
            "product_code": "PROD001",
            "quantity": Decimal("10"),
            "planned_start": date.today(),
            "planned_end": date.today() + timedelta(days=2),
        }, created_by_id=planner_user.id)
        WorkOrderService.release_work_order(wo.id)
        WorkOrderService.complete_work_order(wo.id)
        with pytest.raises(ValidationError, match="Only draft"):
            WorkOrderService.release_work_order(wo.id)

    def test_work_order_is_overdue(self, db_session, planner_user):
        wo = WorkOrderService.create_work_order({
            "order_number": "WO-OVR-001",
            "product_code": "PROD001",
            "quantity": Decimal("10"),
            "planned_start": date(2020, 1, 1),
            "planned_end": date(2020, 1, 7),   # In the past
        }, created_by_id=planner_user.id)
        WorkOrderService.release_work_order(wo.id)
        assert wo.is_overdue is True


class TestCapacityRoutes:
    def test_dashboard_requires_login(self, client):
        response = client.get("/capacity/", follow_redirects=False)
        assert response.status_code == 302

    def test_dashboard_accessible_with_login(self, client, planner_user):
        login(client, "planner@test.com", "Planner!Pass1234")
        response = client.get("/capacity/")
        assert response.status_code == 200
        assert b"Capacity" in response.data

    def test_work_centre_list(self, client, planner_user):
        login(client, "planner@test.com", "Planner!Pass1234")
        response = client.get("/capacity/work-centres")
        assert response.status_code == 200

    def test_work_order_list(self, client, planner_user):
        login(client, "planner@test.com", "Planner!Pass1234")
        response = client.get("/capacity/work-orders")
        assert response.status_code == 200
