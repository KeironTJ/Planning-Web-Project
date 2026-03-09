"""
Capacity planning service layer.

All business rules for capacity planning live here.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from app.extensions import db
from app.core.exceptions import NotFoundError, ValidationError, CapacityError
from .models import WorkCentre, CapacityBucket, WorkOrder, Routing
from .repository import (
    WorkCentreRepository, CapacityBucketRepository,
    WorkOrderRepository, RoutingRepository,
)


class WorkCentreService:

    @staticmethod
    def list_work_centres(active_only: bool = True) -> list[WorkCentre]:
        return WorkCentreRepository.get_all(active_only)

    @staticmethod
    def get_work_centre(wc_id: int) -> WorkCentre:
        wc = WorkCentreRepository.get_by_id(wc_id)
        if not wc:
            raise NotFoundError(f"Work centre ID {wc_id} not found.")
        return wc

    @staticmethod
    def create_work_centre(data: dict) -> WorkCentre:
        if WorkCentreRepository.get_by_code(data.get("code", "")):
            raise ValidationError(f"Work centre code '{data['code']}' already exists.")
        return WorkCentreRepository.create(data)

    @staticmethod
    def update_work_centre(wc_id: int, data: dict) -> WorkCentre:
        wc = WorkCentreService.get_work_centre(wc_id)
        return WorkCentreRepository.update(wc, data)

    @staticmethod
    def deactivate_work_centre(wc_id: int) -> None:
        wc = WorkCentreService.get_work_centre(wc_id)
        WorkCentreRepository.delete(wc)


class CapacityService:
    """
    Manages capacity buckets and allocation logic.
    """

    @staticmethod
    def generate_weekly_buckets(
        work_centre_id: int,
        from_date: date,
        weeks: int = 13,  # Default: 1 quarter
    ) -> list[CapacityBucket]:
        """
        Generate weekly capacity buckets for a WorkCentre.

        Starts from the Monday of `from_date` and creates `weeks` buckets.
        Existing buckets for a period are updated (upsert) rather than
        duplicated.
        """
        wc = WorkCentreRepository.get_by_id(work_centre_id)
        if not wc:
            raise NotFoundError(f"Work centre ID {work_centre_id} not found.")

        # Snap to Monday
        monday = from_date - timedelta(days=from_date.weekday())
        buckets = []
        for i in range(weeks):
            period_start = monday + timedelta(weeks=i)
            period_end = period_start + timedelta(days=4)   # Monday–Friday
            # 5 working days per week
            available_hours = wc.daily_capacity_hours * 5
            bucket = CapacityBucketRepository.upsert(
                work_centre_id=work_centre_id,
                period_start=period_start,
                period_end=period_end,
                available_hours=available_hours,
            )
            buckets.append(bucket)
        return buckets

    @staticmethod
    def get_utilisation_summary(from_date: date, to_date: date) -> list[dict]:
        """
        Return utilisation data for all active work centres within a date range.

        Returns a list of dicts suitable for charting or tabular display.
        """
        buckets = CapacityBucketRepository.get_all_for_period(from_date, to_date)
        summary: dict[int, dict] = {}

        for b in buckets:
            wc_id = b.work_centre_id
            if wc_id not in summary:
                summary[wc_id] = {
                    "work_centre_id": wc_id,
                    "work_centre_name": b.work_centre.name,
                    "work_centre_code": b.work_centre.code,
                    "total_available": Decimal("0"),
                    "total_allocated": Decimal("0"),
                }
            summary[wc_id]["total_available"] += Decimal(str(b.available_hours))
            summary[wc_id]["total_allocated"] += Decimal(str(b.allocated_hours))

        result = []
        for data in summary.values():
            avail = data["total_available"]
            alloc = data["total_allocated"]
            data["utilisation_pct"] = float(alloc / avail * 100) if avail else 0.0
            data["remaining_hours"] = float(avail - alloc)
            result.append(data)

        return sorted(result, key=lambda x: x["utilisation_pct"], reverse=True)

    @staticmethod
    def allocate_hours(bucket_id: int, hours: Decimal) -> CapacityBucket:
        """
        Add `hours` to a capacity bucket's allocated total.

        Raises CapacityError if allocation would exceed available hours.
        """
        bucket = CapacityBucket.query.get(bucket_id)
        if not bucket:
            raise NotFoundError(f"Capacity bucket ID {bucket_id} not found.")

        new_total = Decimal(str(bucket.allocated_hours)) + hours
        if new_total > Decimal(str(bucket.available_hours)):
            raise CapacityError(
                f"Allocation of {hours}h would exceed available capacity "
                f"({bucket.remaining_hours}h remaining)."
            )
        bucket.allocated_hours = new_total
        db.session.commit()
        return bucket


class WorkOrderService:

    @staticmethod
    def list_work_orders(status: Optional[str] = None, page: int = 1, per_page: int = 25):
        return WorkOrderRepository.get_all(status=status, page=page, per_page=per_page)

    @staticmethod
    def get_work_order(wo_id: int) -> WorkOrder:
        wo = WorkOrderRepository.get_by_id(wo_id)
        if not wo:
            raise NotFoundError(f"Work order ID {wo_id} not found.")
        return wo

    @staticmethod
    def create_work_order(data: dict, created_by_id: int) -> WorkOrder:
        if not data.get("order_number"):
            raise ValidationError("Order number is required.")
        if WorkOrderRepository.get_by_number(data["order_number"]):
            raise ValidationError(f"Order number '{data['order_number']}' already exists.")

        data["created_by_id"] = created_by_id
        data.setdefault("status", WorkOrder.STATUS_DRAFT)
        return WorkOrderRepository.create(data)

    @staticmethod
    def update_work_order(wo_id: int, data: dict) -> WorkOrder:
        wo = WorkOrderService.get_work_order(wo_id)
        if "status" in data and data["status"] not in WorkOrder.VALID_STATUSES:
            raise ValidationError(f"Invalid status '{data['status']}'.")
        return WorkOrderRepository.update(wo, data)

    @staticmethod
    def release_work_order(wo_id: int) -> WorkOrder:
        wo = WorkOrderService.get_work_order(wo_id)
        if wo.status != WorkOrder.STATUS_DRAFT:
            raise ValidationError("Only draft work orders can be released.")
        return WorkOrderRepository.update(wo, {"status": WorkOrder.STATUS_RELEASED})

    @staticmethod
    def complete_work_order(wo_id: int) -> WorkOrder:
        wo = WorkOrderService.get_work_order(wo_id)
        if wo.status not in (WorkOrder.STATUS_RELEASED, WorkOrder.STATUS_IN_PROGRESS):
            raise ValidationError("Only released or in-progress orders can be completed.")
        today = date.today()
        return WorkOrderRepository.update(wo, {
            "status": WorkOrder.STATUS_COMPLETED,
            "actual_end": today,
            "quantity_completed": wo.quantity,
        })

    @staticmethod
    def get_open_orders_summary() -> dict:
        orders = WorkOrderRepository.get_open_orders()
        overdue = [o for o in orders if o.is_overdue]
        return {
            "total_open": len(orders),
            "overdue": len(overdue),
            "in_progress": sum(1 for o in orders if o.status == WorkOrder.STATUS_IN_PROGRESS),
            "released": sum(1 for o in orders if o.status == WorkOrder.STATUS_RELEASED),
        }
