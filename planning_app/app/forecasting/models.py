"""Persistent model labels, independent of refreshed COOIS records."""

from datetime import datetime, timezone

from app.extensions import db


class ModelCategory(db.Model):
    """User-managed descriptive group, with no built-in forecasting behaviour."""

    __tablename__ = "forecast_model_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    name_key = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    legacy_value = db.Column(db.String(20), nullable=True, unique=True)

    model_classifications = db.relationship(
        "ModelClassification", back_populates="category"
    )


class ModelClassification(db.Model):
    """Assign an exact model name to a user-managed descriptive category.

    Names are trimmed but case-sensitive. Missing decisions remain unclassified,
    rather than implicitly treating every named model as equivalent. This table
    is separate from WorksOrder so COOIS refreshes retain decisions. A category
    is organizational metadata and does not directly control forecast inclusion.
    """

    __tablename__ = "forecast_model_classifications"

    id = db.Column(db.Integer, primary_key=True)
    model = db.Column(db.String(255), nullable=False, unique=True)
    category_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "forecast_model_categories.id",
            ondelete="RESTRICT",
            name="fk_forecast_model_classifications_category_id",
        ),
        nullable=False,
        index=True,
    )
    updated_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    category = db.relationship("ModelCategory", back_populates="model_classifications")
