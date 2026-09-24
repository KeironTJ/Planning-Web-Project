"""
Material class label management.

Lets planners give human-readable names to opaque Epicor Part_ClassID codes
(e.g. "A101" -> "Upholstery Leather") for display across the shortage reports.
"""
from __future__ import annotations

from typing import Optional

from app.extensions import db
from ..models import MaterialClassLabel

__all__ = ["get_class_labels", "get_class_label_map", "set_class_label", "delete_class_label"]


def get_class_labels():
    """Return all configured class labels, ordered by class_id."""
    return MaterialClassLabel.query.order_by(MaterialClassLabel.class_id).all()


def get_class_label_map() -> dict[str, str]:
    """Return {class_id: label} for all configured labels — used for display lookups."""
    return {row.class_id: row.label for row in MaterialClassLabel.query.all()}


def set_class_label(class_id: str, label: str) -> Optional[MaterialClassLabel]:
    """
    Create or update the label for a class ID. Deletes the row if label is blank.

    Returns the row (or None if deleted/blank input).
    """
    class_id = (class_id or "").strip()
    label = (label or "").strip()
    if not class_id:
        return None
    if not label:
        delete_class_label(class_id)
        return None
    row = db.session.get(MaterialClassLabel, class_id)
    if row:
        row.label = label
    else:
        row = MaterialClassLabel(class_id=class_id, label=label)
        db.session.add(row)
    db.session.commit()
    return row


def delete_class_label(class_id: str) -> bool:
    """Remove the label for a class ID. Returns True if a row was deleted."""
    row = db.session.get(MaterialClassLabel, (class_id or "").strip())
    if not row:
        return False
    db.session.delete(row)
    db.session.commit()
    return True
