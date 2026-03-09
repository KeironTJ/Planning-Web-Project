"""
Demand forecasting models.

Forecasts can be generated algorithmically (moving average, trend) or
entered manually by planners.  A ForecastRun captures one complete
execution of a forecast method; ForecastItem holds individual product/
period predictions.
"""

from datetime import datetime, timezone
from decimal import Decimal
from app.extensions import db


class ForecastRun(db.Model):
    """A named forecasting run / scenario."""

    __tablename__ = "forecast_runs"

    METHOD_MANUAL = "manual"
    METHOD_MOVING_AVG = "moving_average"
    METHOD_TREND = "trend"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    method = db.Column(db.String(30), default=METHOD_MANUAL, nullable=False)
    description = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, default=True)

    items = db.relationship("ForecastItem", back_populates="run", cascade="all, delete-orphan")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<ForecastRun {self.name}>"


class ForecastItem(db.Model):
    """A single product/period demand forecast value."""

    __tablename__ = "forecast_items"
    __table_args__ = (
        db.UniqueConstraint("run_id", "product_code", "period_start", name="uq_forecast_item"),
    )

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("forecast_runs.id", ondelete="CASCADE"), nullable=False)
    product_code = db.Column(db.String(50), nullable=False, index=True)
    period_start = db.Column(db.Date, nullable=False, index=True)
    period_end = db.Column(db.Date, nullable=False)
    forecast_qty = db.Column(db.Numeric(12, 2), nullable=False)
    actual_qty = db.Column(db.Numeric(12, 2))          # Filled in after the period closes
    confidence_pct = db.Column(db.Numeric(5, 2))       # Algorithm confidence (0-100)

    run = db.relationship("ForecastRun", back_populates="items")

    @property
    def forecast_error(self):
        """Absolute forecast error (when actuals are available)."""
        if self.actual_qty is None:
            return None
        return abs(Decimal(str(self.forecast_qty)) - Decimal(str(self.actual_qty)))

    def __repr__(self) -> str:
        return f"<ForecastItem {self.product_code} {self.period_start}>"
