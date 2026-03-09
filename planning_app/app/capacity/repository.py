"""
Capacity data-access layer (repository pattern).

All SQLAlchemy queries live here.  Services import from this module —
never query the DB directly from routes or services.

The repository layer makes it easy to swap out the ORM or database backend
in future without touching business logic.
"""

from datetime import date
from typing import Optional
from decimal import Decimal

from app.extensions import db
from .models import WorkCentre, CapacityBucket, WorkOrder, Routing, Operation, BOM


class WorkCentreRepository:

    @staticmethod
    def get_all(active_only: bool = True) -> list[WorkCentre]:
        q = WorkCentre.query
        if active_only:
            q = q.filter_by(is_active=True)
        return q.order_by(WorkCentre.name).all()

    @staticmethod
    def get_by_id(wc_id: int) -> Optional[WorkCentre]:
        return WorkCentre.query.get(wc_id)

    @staticmethod
    def get_by_code(code: str) -> Optional[WorkCentre]:
        return WorkCentre.query.filter_by(code=code).first()

    @staticmethod
    def create(data: dict) -> WorkCentre:
        wc = WorkCentre(**data)
        db.session.add(wc)
        db.session.commit()
        return wc

    @staticmethod
    def update(wc: WorkCentre, data: dict) -> WorkCentre:
        for key, value in data.items():
            setattr(wc, key, value)
        db.session.commit()
        return wc

    @staticmethod
    def delete(wc: WorkCentre) -> None:
        wc.is_active = False   # Soft delete — preserve historical data
        db.session.commit()


class CapacityBucketRepository:

    @staticmethod
    def get_for_period(
        work_centre_id: int,
        start: date,
        end: date,
    ) -> list[CapacityBucket]:
        return (
            CapacityBucket.query
            .filter(
                CapacityBucket.work_centre_id == work_centre_id,
                CapacityBucket.period_start >= start,
                CapacityBucket.period_end <= end,
            )
            .order_by(CapacityBucket.period_start)
            .all()
        )

    @staticmethod
    def get_all_for_period(start: date, end: date) -> list[CapacityBucket]:
        """Load all work centre buckets within a date range (for dashboard)."""
        return (
            CapacityBucket.query
            .join(WorkCentre)
            .filter(
                WorkCentre.is_active == True,
                CapacityBucket.period_start >= start,
                CapacityBucket.period_end <= end,
            )
            .order_by(CapacityBucket.work_centre_id, CapacityBucket.period_start)
            .all()
        )

    @staticmethod
    def upsert(work_centre_id: int, period_start: date, period_end: date, available_hours: Decimal) -> CapacityBucket:
        bucket = CapacityBucket.query.filter_by(
            work_centre_id=work_centre_id,
            period_start=period_start,
        ).first()
        if bucket:
            bucket.available_hours = available_hours
        else:
            bucket = CapacityBucket(
                work_centre_id=work_centre_id,
                period_start=period_start,
                period_end=period_end,
                available_hours=available_hours,
            )
            db.session.add(bucket)
        db.session.commit()
        return bucket


class WorkOrderRepository:

    @staticmethod
    def get_all(status: Optional[str] = None, page: int = 1, per_page: int = 25):
        q = WorkOrder.query
        if status:
            q = q.filter_by(status=status)
        return q.order_by(WorkOrder.planned_start, WorkOrder.priority).paginate(
            page=page, per_page=per_page, error_out=False
        )

    @staticmethod
    def get_by_id(wo_id: int) -> Optional[WorkOrder]:
        return WorkOrder.query.get(wo_id)

    @staticmethod
    def get_by_number(order_number: str) -> Optional[WorkOrder]:
        return WorkOrder.query.filter_by(order_number=order_number).first()

    @staticmethod
    def get_open_orders() -> list[WorkOrder]:
        return (
            WorkOrder.query
            .filter(WorkOrder.status.in_([WorkOrder.STATUS_RELEASED, WorkOrder.STATUS_IN_PROGRESS]))
            .order_by(WorkOrder.priority, WorkOrder.planned_end)
            .all()
        )

    @staticmethod
    def create(data: dict) -> WorkOrder:
        wo = WorkOrder(**data)
        db.session.add(wo)
        db.session.commit()
        return wo

    @staticmethod
    def update(wo: WorkOrder, data: dict) -> WorkOrder:
        for key, value in data.items():
            setattr(wo, key, value)
        db.session.commit()
        return wo


class RoutingRepository:

    @staticmethod
    def get_all(active_only: bool = True) -> list[Routing]:
        q = Routing.query
        if active_only:
            q = q.filter_by(is_active=True)
        return q.order_by(Routing.code).all()

    @staticmethod
    def get_by_id(routing_id: int) -> Optional[Routing]:
        return Routing.query.get(routing_id)

    @staticmethod
    def get_by_code(code: str) -> Optional[Routing]:
        return Routing.query.filter_by(code=code).first()
