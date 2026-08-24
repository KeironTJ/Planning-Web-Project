"""
Request-level MRP report cache.

Isolated from netting.py so the core engine has no Flask dependency.
Multiple callers within the same request share one netting pass per material group.
"""
from __future__ import annotations

from .netting import get_shortage_report

__all__ = ["_cached_group_report", "_cached_unfiltered_report"]


def _cached_group_report(group: str) -> dict:
    """Return the full unfiltered report for `group`, cached on flask.g for this request."""
    cache_key = f"_mrp_{group}_cache"
    try:
        from flask import g
        if not hasattr(g, cache_key):
            setattr(g, cache_key, get_shortage_report(material_group=group, shortages_only=False))
        return getattr(g, cache_key)
    except RuntimeError:
        # Outside a request context (e.g. CLI/tests) — compute directly.
        return get_shortage_report(material_group=group, shortages_only=False)


def _cached_unfiltered_report() -> dict:
    """Fabric-group report, shared across all callers on the same request."""
    return _cached_group_report("fabric")
