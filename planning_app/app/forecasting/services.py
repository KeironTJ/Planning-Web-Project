"""Model classification and a review of the currently imported COOIS snapshot."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from app.auth.models import AuditLog
from app.core.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.operations.models import WorksOrder

from .models import MODEL_CATEGORIES, ModelClassification


@dataclass
class ModelReview:
    """One exact model value and evidence for reviewing one-piece planning."""

    model: str
    category: str
    record_count: int = 0
    required_qty_total: Decimal = Decimal("0")
    missing_qty_count: int = 0
    non_single_qty_count: int = 0


def classify_model(model: str | None, mapping: Mapping[str, str]) -> str:
    """Apply exact classifications; blank models always remain excluded.

    No substring or prefix rules are inferred from names, including ACO, LCO,
    and Scatter. An unseen nonblank value needs a business decision.
    """
    name = (model or "").strip()
    if not name:
        return "excluded"
    return mapping.get(name, "unclassified")


def get_model_review() -> list[ModelReview]:
    """Compare saved decisions with model values in the live COOIS snapshot.

    Counts describe imported job/assembly records, including completed jobs if
    present in that snapshot. They are not historical or weekly forecast totals.
    Required quantity is shown as supporting evidence rather than silently
    replacing the owner's one-piece counting convention.
    """
    classifications = ModelClassification.query.order_by(
        ModelClassification.model
    ).all()
    mapping = {item.model: item.category for item in classifications}
    review = {
        item.model: ModelReview(item.model, item.category)
        for item in classifications
    }
    records = db.session.query(WorksOrder.model, WorksOrder.required_qty).all()
    for model, required_qty in records:
        name = (model or "").strip()
        if name not in review:
            review[name] = ModelReview(name, classify_model(name, mapping))
        item = review[name]
        item.record_count += 1
        if required_qty is None:
            item.missing_qty_count += 1
        else:
            item.required_qty_total += required_qty
            if required_qty != Decimal("1"):
                item.non_single_qty_count += 1
    return sorted(review.values(), key=lambda item: item.model)


def save_model_classification(
    model: str, category: str, user_id: int
) -> ModelClassification:
    """Save a validated classification and its audit entry in one transaction."""
    name = model.strip()
    if not name:
        raise ValidationError("A model name is required. Blank models are excluded.")
    if len(name) > 255:
        raise ValidationError("Model names must be 255 characters or fewer.")
    if category not in MODEL_CATEGORIES:
        raise ValidationError("Choose full unit, cover-only, Scatter, or excluded.")

    item = ModelClassification.query.filter_by(model=name).first()
    previous = item.category if item else "unclassified"
    if item is None:
        item = ModelClassification(model=name)
        db.session.add(item)
    item.category = category
    item.updated_by_id = user_id
    item.updated_at = datetime.now(timezone.utc)
    db.session.add(
        AuditLog(
            user_id=user_id,
            action="forecast_model_classification_saved",
            resource="forecast_model_classification",
            details=json.dumps(
                {"model": name, "previous": previous, "category": category}
            ),
        )
    )
    db.session.commit()
    return item


def remove_model_classification(model: str, user_id: int) -> None:
    """Remove a decision so the model can be reviewed again as unclassified."""
    item = ModelClassification.query.filter_by(model=model.strip()).first()
    if item is None:
        raise NotFoundError("This model has no saved classification.")
    db.session.add(
        AuditLog(
            user_id=user_id,
            action="forecast_model_classification_removed",
            resource="forecast_model_classification",
            details=json.dumps({"model": item.model, "previous": item.category}),
        )
    )
    db.session.delete(item)
    db.session.commit()
