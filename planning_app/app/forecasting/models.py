"""Persistent product classifications, independent of refreshed COOIS records."""

from datetime import datetime, timezone

from app.extensions import db


MODEL_CATEGORIES = {
    "full_unit": "Full unit",
    "cover_only": "Cover-only",
    "scatter": "Scatter",
    "excluded": "Excluded",
}


class ModelClassification(db.Model):
    """An exact model-name decision used to prepare forecasting unit targets.

    Names are trimmed but case-sensitive. Missing decisions remain unclassified,
    rather than implicitly turning every named model into a finished unit.
    This table is separate from WorksOrder so COOIS refreshes retain decisions.
    """

    __tablename__ = "forecast_model_classifications"
    __table_args__ = (
        db.CheckConstraint(
            "category IN ('full_unit', 'cover_only', 'scatter', 'excluded')",
            name="ck_forecast_model_category",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    model = db.Column(db.String(255), nullable=False, unique=True)
    category = db.Column(db.String(20), nullable=False)
    updated_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
