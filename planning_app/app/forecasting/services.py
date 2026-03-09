"""Forecasting service layer."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from flask_login import current_user

from app.extensions import db
from app.core.exceptions import NotFoundError, ValidationError
from .models import ForecastRun, ForecastItem


class ForecastService:

    @staticmethod
    def list_runs(active_only: bool = True) -> list[ForecastRun]:
        q = ForecastRun.query
        if active_only:
            q = q.filter_by(is_active=True)
        return q.order_by(ForecastRun.created_at.desc()).all()

    @staticmethod
    def get_run(run_id: int) -> ForecastRun:
        run = ForecastRun.query.get(run_id)
        if not run:
            raise NotFoundError(f"Forecast run ID {run_id} not found.")
        return run

    @staticmethod
    def create_run(name: str, method: str, description: str = "") -> ForecastRun:
        run = ForecastRun(
            name=name,
            method=method,
            description=description,
            created_by_id=current_user.id if current_user.is_authenticated else None,
        )
        db.session.add(run)
        db.session.commit()
        return run

    @staticmethod
    def generate_moving_average(
        run_id: int,
        product_code: str,
        historical_qtys: list[Decimal],
        periods_ahead: int = 12,
        window: int = 3,
    ) -> list[ForecastItem]:
        """
        Generate a simple moving-average forecast.

        Args:
            run_id:          The ForecastRun to attach items to.
            product_code:    Product being forecast.
            historical_qtys: Past period quantities (oldest first).
            periods_ahead:   How many future weekly periods to forecast.
            window:          Moving average window size.

        Returns:
            List of ForecastItem objects (not yet committed).
        """
        run = ForecastService.get_run(run_id)
        if len(historical_qtys) < window:
            raise ValidationError(f"Need at least {window} historical periods for a window of {window}.")

        # Compute moving average from the last `window` periods
        avg = sum(historical_qtys[-window:]) / window
        today = date.today()
        monday = today - timedelta(days=today.weekday())

        items = []
        for i in range(periods_ahead):
            period_start = monday + timedelta(weeks=i)
            period_end = period_start + timedelta(days=6)
            item = ForecastItem(
                run_id=run_id,
                product_code=product_code,
                period_start=period_start,
                period_end=period_end,
                forecast_qty=round(avg, 2),
                confidence_pct=Decimal("70.00"),
            )
            db.session.add(item)
            items.append(item)

        db.session.commit()
        return items

    @staticmethod
    def get_forecast_for_product(product_code: str, run_id: Optional[int] = None) -> list[ForecastItem]:
        """Retrieve forecast items for a product, optionally filtered by run."""
        q = ForecastItem.query.filter_by(product_code=product_code)
        if run_id:
            q = q.filter_by(run_id=run_id)
        return q.order_by(ForecastItem.period_start).all()
