"""
MRP exempt material management.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.extensions import db
from ..models import MrpExemptMaterial

__all__ = ["get_exempt_materials", "add_exemptions", "remove_exemptions"]


def get_exempt_materials(search: Optional[str] = None):
    """Return all exempt materials, optionally filtered by code/reason."""
    q = MrpExemptMaterial.query.order_by(MrpExemptMaterial.material_code)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                MrpExemptMaterial.material_code.ilike(term),
                MrpExemptMaterial.reason.ilike(term),
            )
        )
    return q.all()


def add_exemptions(codes: list[str], reason: Optional[str], user_id: Optional[int]) -> dict:
    """
    Add material codes to the exempt list. Ignores duplicates.

    Returns {"added": int, "skipped": int}.
    """
    added = skipped = 0
    reason = reason.strip() if reason else None
    for raw in codes:
        code = raw.strip().upper()
        if not code:
            continue
        if db.session.get(MrpExemptMaterial, code):
            skipped += 1
        else:
            db.session.add(MrpExemptMaterial(
                material_code=code,
                reason=reason,
                exempted_at=datetime.now(timezone.utc),
                exempted_by_id=user_id,
            ))
            added += 1
    db.session.commit()
    return {"added": added, "skipped": skipped}


def remove_exemptions(codes: list[str]) -> int:
    """Remove material codes from the exempt list. Returns count deleted."""
    deleted = 0
    for raw in codes:
        code = raw.strip().upper()
        if not code:
            continue
        obj = db.session.get(MrpExemptMaterial, code)
        if obj:
            db.session.delete(obj)
            deleted += 1
    db.session.commit()
    return deleted
