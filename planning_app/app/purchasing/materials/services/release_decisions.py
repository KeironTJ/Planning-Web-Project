"""
Durable persistence for Cash Release Impact staging/commit decisions.

PurchaseOrder rows are fully truncated and reloaded on every Epicor sync
(see ``app.core.epicor_importers``) with no stable surrogate key, so
committed/staged decisions live in their own table, keyed on the natural
``(po_num, po_line, po_release)`` business key, so they survive the daily
sync instead of only living in the page's query string.

Lifecycle: staged -> committed -> fulfilled (auto, once the PO drops out of
the open-PO sync / is fully received) or withdrawn (manual, by a user).
``reconcile_release_decisions()`` should be called once at the end of every
sync batch to auto-close decisions whose PO has been received.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from app.extensions import db
from ..models import PurchaseOrder, ReleaseDecision

__all__ = [
    "get_active_committed_keys",
    "get_active_staged_keys",
    "get_staged_snapshot",
    "get_release_decisions",
    "stage_selection",
    "commit_staged",
    "clear_staged",
    "update_committed_selection",
    "reconcile_release_decisions",
]


def _key_of(po_num: int, po_line: int, po_release: int) -> str:
    return f"{po_num}/{po_line}/{po_release}"


def _active_query(status: str):
    return ReleaseDecision.query.filter_by(status=status)


def get_active_committed_keys() -> set[str]:
    """Committed PO keys that should stay in the calculation baseline."""
    return {
        _key_of(r.po_num, r.po_line, r.po_release)
        for r in _active_query(ReleaseDecision.STATUS_COMMITTED).all()
    }


def get_active_staged_keys() -> set[str]:
    """PO keys the user has staged for review but not yet committed."""
    return {
        _key_of(r.po_num, r.po_line, r.po_release)
        for r in _active_query(ReleaseDecision.STATUS_STAGED).all()
    }


def get_release_decisions(statuses: Iterable[str] | None = None) -> list[ReleaseDecision]:
    """All decisions, optionally filtered to a set of statuses."""
    query = ReleaseDecision.query
    if statuses:
        query = query.filter(ReleaseDecision.status.in_(list(statuses)))
    return query.order_by(ReleaseDecision.id.desc()).all()


def get_staged_snapshot() -> Optional[dict]:
    """
    Return the combined-impact snapshot recorded the last time the current
    staged group was reviewed, or ``None`` if nothing is staged.

    All rows in a staged group share the same snapshot because the
    calculated impact is one joint scenario, not a sum of per-PO impacts.
    """
    row = (
        _active_query(ReleaseDecision.STATUS_STAGED)
        .filter(ReleaseDecision.snapshot_taken_at.isnot(None))
        .order_by(ReleaseDecision.snapshot_taken_at.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "cash_proxy": row.snapshot_cash_proxy,
        "jobs_unlocked": row.snapshot_jobs_unlocked,
        "orders_unlocked": row.snapshot_orders_unlocked,
        "order_value_unlocked": row.snapshot_order_value_unlocked,
        "taken_at": row.snapshot_taken_at,
    }


def _get_or_create(po_num: int, po_line: int, po_release: int) -> ReleaseDecision:
    decision = ReleaseDecision.query.filter_by(
        po_num=po_num, po_line=po_line, po_release=po_release
    ).first()
    if decision is None:
        decision = ReleaseDecision(po_num=po_num, po_line=po_line, po_release=po_release)
        db.session.add(decision)
    return decision


def stage_selection(
    candidates: Iterable,
    staged_result: Optional[dict],
    user_id: Optional[int] = None,
) -> None:
    """
    Persist a staged selection, replacing whatever was staged before.

    ``candidates`` are the ``_Candidate`` objects the user ticked (used to
    denormalise material_code/description so they remain readable even if
    the PO later drops out of a sync). ``staged_result`` is the freshly
    calculated combined impact for this exact selection (from
    ``get_release_impact``) — its headline metrics become the drift-
    detection snapshot stored on every row in the group.

    An empty ``candidates`` is equivalent to clearing the staged selection.
    """
    now = datetime.now(timezone.utc)
    selected = list(candidates)
    selected_keys = {candidate.key for candidate in selected}

    # Anything staged previously but not part of this new selection is
    # withdrawn rather than left dangling.
    for decision in _active_query(ReleaseDecision.STATUS_STAGED).all():
        if _key_of(decision.po_num, decision.po_line, decision.po_release) not in selected_keys:
            decision.status = ReleaseDecision.STATUS_WITHDRAWN
            decision.closed_at = now
            decision.closed_reason = "replaced"

    snapshot = staged_result or {}
    for candidate in selected:
        decision = _get_or_create(candidate.po_num, candidate.po_line, candidate.po_release)
        decision.material_code = candidate.material_code
        decision.description = candidate.description
        decision.status = ReleaseDecision.STATUS_STAGED
        decision.staged_at = now
        decision.created_by_id = decision.created_by_id or user_id
        decision.closed_at = None
        decision.closed_reason = None
        decision.snapshot_cash_proxy = snapshot.get("cash_proxy")
        decision.snapshot_jobs_unlocked = len(snapshot.get("jobs_unlocked") or [])
        decision.snapshot_orders_unlocked = len(snapshot.get("orders_unlocked") or [])
        decision.snapshot_order_value_unlocked = snapshot.get("order_value_unlocked")
        decision.snapshot_taken_at = now
    db.session.commit()


def commit_staged(user_id: Optional[int] = None) -> int:
    """Move every currently staged decision to committed. Returns count."""
    now = datetime.now(timezone.utc)
    rows = _active_query(ReleaseDecision.STATUS_STAGED).all()
    for decision in rows:
        decision.status = ReleaseDecision.STATUS_COMMITTED
        decision.committed_at = now
        decision.committed_by_id = user_id
    if rows:
        db.session.commit()
    return len(rows)


def clear_staged() -> int:
    """Withdraw everything currently staged. Returns count cleared."""
    now = datetime.now(timezone.utc)
    rows = _active_query(ReleaseDecision.STATUS_STAGED).all()
    for decision in rows:
        decision.status = ReleaseDecision.STATUS_WITHDRAWN
        decision.closed_at = now
        decision.closed_reason = "cleared"
    if rows:
        db.session.commit()
    return len(rows)


def update_committed_selection(keep_keys: set[str]) -> int:
    """
    Withdraw committed decisions not present in ``keep_keys``.

    Used by the "remove from baseline" panel, where a user un-ticks a
    previously committed PO to exclude it from the baseline again. Returns
    the number removed.
    """
    now = datetime.now(timezone.utc)
    removed = 0
    for decision in _active_query(ReleaseDecision.STATUS_COMMITTED).all():
        if _key_of(decision.po_num, decision.po_line, decision.po_release) not in keep_keys:
            decision.status = ReleaseDecision.STATUS_WITHDRAWN
            decision.closed_at = now
            decision.closed_reason = "removed"
            removed += 1
    if removed:
        db.session.commit()
    return removed


def reconcile_release_decisions() -> dict:
    """
    Auto-close committed/staged decisions whose PO has dropped out of the
    latest sync (i.e. it is fully received and no longer an open release).

    Call this once at the end of a sync batch, after the purchase_orders
    importer has run. Committed decisions "drop off" here because their PO
    is now stock rather than an outstanding release; staged decisions are
    closed the same way since there is nothing left to decide once a PO has
    been received.

    Returns ``{"checked": n, "fulfilled": n}`` for logging/notification.
    """
    now = datetime.now(timezone.utc)
    active = ReleaseDecision.query.filter(
        ReleaseDecision.status.in_(
            [ReleaseDecision.STATUS_STAGED, ReleaseDecision.STATUS_COMMITTED]
        )
    ).all()
    if not active:
        return {"checked": 0, "fulfilled": 0}

    po_nums = {d.po_num for d in active}
    open_pos = {
        (po.po_num, po.po_line, po.po_release): po
        for po in PurchaseOrder.query.filter(PurchaseOrder.po_num.in_(po_nums)).all()
    }
    fulfilled = 0
    for decision in active:
        po = open_pos.get((decision.po_num, decision.po_line, decision.po_release))
        # A missing row means the release no longer appears in the synced
        # open-PO set at all. A present row with zero (or unexpectedly
        # negative) outstanding qty means it has been fully received.
        still_open = po is not None and (
            po.outstanding_qty is None or po.outstanding_qty > 0
        )
        if not still_open:
            decision.status = ReleaseDecision.STATUS_FULFILLED
            decision.closed_at = now
            decision.closed_reason = "received"
            fulfilled += 1
    if fulfilled:
        db.session.commit()
    return {"checked": len(active), "fulfilled": fulfilled}
