"""Model-category maintenance and review of the imported COOIS snapshot."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.auth.models import AuditLog
from app.core.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.operations.models import WorksOrder

from .models import ModelCategory, ModelClassification


@dataclass
class ModelReview:
    """One exact model name and evidence for reviewing one-piece planning."""

    model: str
    category_id: int | None = None
    category_name: str = "Unclassified"
    record_count: int = 0
    required_qty_total: Decimal = Decimal("0")
    missing_qty_count: int = 0
    non_single_qty_count: int = 0


def get_model_categories() -> list[ModelCategory]:
    """Return user-managed categories in their configured display order."""
    return ModelCategory.query.order_by(
        ModelCategory.sort_order, ModelCategory.name_key
    ).all()


def get_model_review() -> list[ModelReview]:
    """Compare saved category labels with model values in the live COOIS snapshot.

    Counts describe imported job/assembly records, including completed jobs if
    present in that snapshot. They are not historical or weekly forecast totals.
    Required quantity is supporting evidence rather than an implicit forecast
    inclusion rule.
    """
    categories = get_model_categories()
    classifications = ModelClassification.query.all()
    mapping = {
        item.model: ModelReview(
            model=item.model,
            category_id=item.category_id,
            category_name=item.category.name,
        )
        for item in classifications
    }
    records = db.session.query(WorksOrder.model, WorksOrder.required_qty).all()
    for model, required_qty in records:
        name = (model or "").strip()
        if name not in mapping:
            mapping[name] = ModelReview(model=name)
        item = mapping[name]
        item.record_count += 1
        if required_qty is None:
            item.missing_qty_count += 1
        else:
            item.required_qty_total += required_qty
            if required_qty != Decimal("1"):
                item.non_single_qty_count += 1

    category_rank = {category.id: rank for rank, category in enumerate(categories)}
    return sorted(
        mapping.values(),
        key=lambda item: (
            category_rank.get(item.category_id, len(category_rank)),
            item.category_name.casefold(),
            item.model.casefold(),
        ),
    )


def save_model_category(
    name: str,
    description: str,
    user_id: int,
    category_id: int | None = None,
) -> ModelCategory:
    """Create or rename a descriptive category without assigning forecast meaning."""
    category_name = name.strip()
    category_description = description.strip() or None
    if not category_name:
        raise ValidationError("A category name is required.")
    if len(category_name) > 80:
        raise ValidationError("Category names must be 80 characters or fewer.")
    name_key = category_name.casefold()
    if len(name_key) > 80:
        raise ValidationError("Category names must be 80 characters or fewer.")
    if category_description and len(category_description) > 255:
        raise ValidationError("Category descriptions must be 255 characters or fewer.")

    duplicate = ModelCategory.query.filter_by(name_key=name_key).first()
    if duplicate and duplicate.id != category_id:
        raise ValidationError("A category with this name already exists.")

    category = db.session.get(ModelCategory, category_id) if category_id else None
    if category_id and category is None:
        raise NotFoundError("The category no longer exists.")

    is_new = category is None
    if is_new:
        last_order = db.session.query(db.func.max(ModelCategory.sort_order)).scalar()
        category = ModelCategory(
            sort_order=(last_order if last_order is not None else -1) + 1
        )
        db.session.add(category)
    old_values = (
        {"name": category.name, "description": category.description}
        if not is_new
        else None
    )
    category.name = category_name
    category.name_key = name_key
    category.description = category_description
    db.session.flush()
    db.session.add(
        AuditLog(
            user_id=user_id,
            action="forecast_model_category_saved",
            resource="forecast_model_category",
            details=json.dumps({
                "category_id": category.id,
                "previous": old_values,
                "name": category_name,
                "description": category_description,
            }),
        )
    )
    db.session.commit()
    return category


def remove_model_category(category_id: int, user_id: int) -> None:
    """Remove an unused category; assigned models must be moved first."""
    category = db.session.get(ModelCategory, category_id)
    if category is None:
        raise NotFoundError("The category no longer exists.")
    if ModelClassification.query.filter_by(category_id=category_id).first():
        raise ValidationError(
            "Move the models in this category before deleting it."
        )
    db.session.add(
        AuditLog(
            user_id=user_id,
            action="forecast_model_category_removed",
            resource="forecast_model_category",
            details=json.dumps({"category_id": category.id, "name": category.name}),
        )
    )
    db.session.delete(category)
    db.session.commit()


def _get_category(category_id: int) -> ModelCategory:
    """Return an existing category or raise an actionable validation error."""
    if category_id is None:
        raise ValidationError("Choose an available category.")
    category = db.session.get(ModelCategory, category_id)
    if category is None:
        raise ValidationError("Choose an available category.")
    return category


def save_model_classification(
    model: str, category_id: int, user_id: int
) -> ModelClassification:
    """Assign an exact model name to one user-managed category."""
    name = model.strip()
    if not name:
        raise ValidationError("A model name is required.")
    if len(name) > 255:
        raise ValidationError("Model names must be 255 characters or fewer.")
    category = _get_category(category_id)
    item = ModelClassification.query.filter_by(model=name).first()
    previous = (
        {"category_id": item.category_id, "category_name": item.category.name}
        if item
        else None
    )
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
            details=json.dumps({
                "model": name,
                "previous": previous,
                "category_id": category.id,
                "category_name": category.name,
            }),
        )
    )
    db.session.commit()
    return item


def save_model_classifications(
    models: list[str], category_id: int, user_id: int
) -> int:
    """Assign several exact model names to one category in a single transaction."""
    names = list(dict.fromkeys(model.strip() for model in models))
    if not names:
        raise ValidationError("Select at least one model to classify.")
    if len(names) > 500:
        raise ValidationError("Classify no more than 500 models at a time.")
    if any(not name for name in names):
        raise ValidationError("Blank models cannot be classified.")
    if any(len(name) > 255 for name in names):
        raise ValidationError("Model names must be 255 characters or fewer.")
    category = _get_category(category_id)

    items = {
        item.model: item
        for item in ModelClassification.query.filter(
            ModelClassification.model.in_(names)
        ).all()
    }
    previous = {
        name: (
            {
                "category_id": items[name].category_id,
                "category_name": items[name].category.name,
            }
            if name in items
            else None
        )
        for name in names
    }
    now = datetime.now(timezone.utc)
    for name in names:
        item = items.get(name)
        if item is None:
            item = ModelClassification(model=name)
            db.session.add(item)
        item.category = category
        item.updated_by_id = user_id
        item.updated_at = now

    db.session.add(
        AuditLog(
            user_id=user_id,
            action="forecast_model_classifications_bulk_saved",
            resource="forecast_model_classification",
            details=json.dumps({
                "models": names,
                "previous": previous,
                "category_id": category.id,
                "category_name": category.name,
            }),
        )
    )
    db.session.commit()
    return len(names)


def save_model_classification_updates(
    updates: list[tuple[str, int | None]], user_id: int
) -> int:
    """Apply changed model categories together as one audited transaction."""
    if not updates:
        raise ValidationError("There are no model changes to save.")
    if len(updates) > 500:
        raise ValidationError("Update no more than 500 models at a time.")

    names = [model.strip() for model, _ in updates]
    if any(len(name) > 255 for name in names):
        raise ValidationError("Model names must be 255 characters or fewer.")
    if len(set(names)) != len(names):
        raise ValidationError("Each model can only be updated once per save.")

    categories = {
        category_id: _get_category(category_id)
        for category_id in {category_id for _, category_id in updates}
        if category_id is not None
    }
    selected_categories = {
        name: categories.get(category_id)
        for name, (_, category_id) in zip(names, updates)
    }
    items = {
        item.model: item
        for item in ModelClassification.query.filter(
            ModelClassification.model.in_(names)
        ).all()
    }

    previous = {}
    assignments = {}
    now = datetime.now(timezone.utc)
    changed = 0
    for name in names:
        item = items.get(name)
        category = selected_categories[name]
        if item is not None and item.category_id == (
            category.id if category is not None else None
        ):
            continue
        if item is None and category is None:
            continue

        previous[name] = (
            {
                "category_id": item.category_id,
                "category_name": item.category.name,
            }
            if item is not None
            else None
        )
        assignments[name] = (
            {"category_id": category.id, "category_name": category.name}
            if category is not None
            else None
        )
        if category is None:
            db.session.delete(item)
        else:
            if item is None:
                item = ModelClassification(model=name)
                db.session.add(item)
            item.category = category
            item.updated_by_id = user_id
            item.updated_at = now
        changed += 1

    if changed == 0:
        return 0
    db.session.add(
        AuditLog(
            user_id=user_id,
            action="forecast_model_classifications_bulk_saved",
            resource="forecast_model_classification",
            details=json.dumps({
                "models": list(assignments),
                "previous": previous,
                "assignments": assignments,
            }),
        )
    )
    db.session.commit()
    return changed


def remove_model_classification(model: str, user_id: int) -> None:
    """Remove a model's category assignment so it can be reviewed again."""
    item = ModelClassification.query.filter_by(model=model.strip()).first()
    if item is None:
        raise NotFoundError("This model has no saved classification.")
    db.session.add(
        AuditLog(
            user_id=user_id,
            action="forecast_model_classification_removed",
            resource="forecast_model_classification",
            details=json.dumps({
                "model": item.model,
                "previous_category_id": item.category_id,
                "previous_category_name": item.category.name,
            }),
        )
    )
    db.session.delete(item)
    db.session.commit()
