"""
Works-order capacity planning service layer.

Core function: get_planning_workspace()
  - Loads FIRM / RELEASED / ALL works orders (never modifies live data).
  - Applies per-job PlanningOverrides from the given session at read time.
  - Computes demand load (units; SMV when dept has smv_per_unit configured).
  - Joins with CapacityBucket available hours.
  - Returns a single dict consumed by the workspace template and JSON endpoint.

Secondary helpers:
  - Session CRUD (create, clone, deactivate/delete)
  - PlanningOverride upsert / delete
  - DMT export builder (stub — column mapping to be provided)
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Optional

from app.extensions import db
from app.sales.orders.models import Department
from app.operations.models import WorksOrder
from app.planning.capacity.services import (
    get_available_by_week_dept,
    _week_label,        # noqa: F401
    _week_start,        # noqa: F401
    _period_weeks,
)
from app.purchasing.materials.services.status import get_so_material_status
from app.purchasing.materials.services.types import MAT_STATUS_META, _MAT_STATUS_PRIORITY


# ---------------------------------------------------------------------------
# Epicor prod_plnwk decoder
# ---------------------------------------------------------------------------

def _epicor_plnwk_to_iso(plnwk: str) -> str:
    """
    Convert Epicor's 6-character prod_plnwk code to an ISO week label.

    Epicor format: YYMMDD where
        YY  = 2-digit year (e.g. 26 → 2026)
        MM  = ISO week number (01–53)
        DD  = day-of-week within that week (00 = week-start; 01–06 = Mon–Sat)

    Examples
        '263704'  →  '2026-W37'
        '263900'  →  '2026-W39'
        '262306'  →  '2026-W23'

    Returns the raw value unchanged if it is not exactly 6 numeric characters.
    """
    if not plnwk or len(plnwk) != 6:
        return plnwk or ""
    try:
        year = 2000 + int(plnwk[0:2])
        week = int(plnwk[2:4])
        return f"{year}-W{week:02d}"
    except (ValueError, IndexError):
        return plnwk
from .models import (
    PlanningSession, PlanningOverride,
    PlanningCapacityTarget, CustomerGroup, CustomerGroupMember,
    CustomerGroupCapacityTarget,
)


# ---------------------------------------------------------------------------
# State-filter constants
# ---------------------------------------------------------------------------

FILTER_FIRM     = "firm"
FILTER_RELEASED = "released"
FILTER_ALL      = "all"
FILTER_UNFIRM   = "unfirm"


# ---------------------------------------------------------------------------
# Capacity target helpers
# ---------------------------------------------------------------------------

def get_capacity_targets(week_labels: list[str]) -> dict[str, dict]:
    """
    Return a dict of week_label → {target, max, target_per_day, working_days}
    for each week in week_labels.

    Lookup order: week-specific row first, then the NULL-week default row.
    Returns zeros if neither exists.
    """
    # Load all rows covering our weeks + the default (NULL week)
    rows = PlanningCapacityTarget.query.filter(
        db.or_(
            PlanningCapacityTarget.week.in_(week_labels),
            PlanningCapacityTarget.week.is_(None),
        )
    ).all()

    default_row = next((r for r in rows if r.week is None), None)
    specific    = {r.week: r for r in rows if r.week is not None}

    result = {}
    for wk in week_labels:
        row = specific.get(wk) or default_row
        if row:
            result[wk] = {
                "target":         round(row.weekly_target, 1),
                "max":            round(row.weekly_max, 1),
                "target_per_day": float(row.target_per_day or 0),
                "working_days":   int(row.working_days or 0),
                "is_override":    wk in specific,
                "is_set":         True,   # a capacity row exists (default or override)
            }
        else:
            result[wk] = {
                "target": 0, "max": 0,
                "target_per_day": 0, "working_days": 4,
                "is_override": False, "is_set": False,
            }
    return result


def get_default_target() -> Optional[PlanningCapacityTarget]:
    return PlanningCapacityTarget.query.filter_by(week=None).first()


def get_target_for_week(week: str) -> Optional[PlanningCapacityTarget]:
    return PlanningCapacityTarget.query.filter_by(week=week).first()


def save_capacity_target(
    week: Optional[str],
    target_per_day: float,
    working_days: int,
    max_per_day: Optional[float],
    notes: Optional[str],
    user_id: Optional[int],
) -> PlanningCapacityTarget:
    """Upsert a capacity target for a specific week or the default (week=None)."""
    row = PlanningCapacityTarget.query.filter_by(week=week).first()
    if not row:
        row = PlanningCapacityTarget(week=week)
        db.session.add(row)
    row.target_per_day = target_per_day
    row.working_days   = working_days
    row.max_per_day    = max_per_day or None
    row.notes          = notes or None
    row.updated_by_id  = user_id
    row.updated_at     = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    db.session.commit()
    return row


def delete_capacity_target(target_id: int) -> None:
    row = PlanningCapacityTarget.query.get_or_404(target_id)
    db.session.delete(row)
    db.session.commit()


def get_iso_weeks_for_year(year: int) -> list[dict]:
    """
    Return all ISO weeks that belong to `year` as a list of dicts:
      {label, week_num, week_start, week_end, date_range}

    ISO year can differ from calendar year (weeks 1 and 52/53 boundary).
    """
    from datetime import timedelta as _td

    # Find the Monday of ISO week 1 for this year
    jan4 = date(year, 1, 4)               # Jan 4 is always in week 1
    week1_mon = jan4 - _td(days=jan4.weekday())

    weeks = []
    current = week1_mon
    while True:
        iso = current.isocalendar()
        if iso[0] != year:
            break
        label    = f"{iso[0]}-W{iso[1]:02d}"
        week_end = current + _td(days=6)
        weeks.append({
            "label":      label,
            "week_num":   iso[1],
            "week_start": current,
            "week_end":   week_end,
            "date_range": f"{current.strftime('%d/%m')} – {week_end.strftime('%d/%m/%y')}",
        })
        current += _td(weeks=1)
    return weeks


def get_year_capacity_grid(year: int) -> tuple[list[dict], list[CustomerGroup]]:
    """
    Return a row for every ISO week in `year`, plus active groups.

    Each row: {label, week_num, week_start, week_end, date_range,
               target_per_day, working_days, weekly_sum, is_override,
               is_current, group_caps: {group_id: {cap, is_override}}}

    Returns (rows, active_groups).
    """
    weeks      = get_iso_weeks_for_year(year)
    labels     = [w["label"] for w in weeks]
    target_map = get_capacity_targets(labels)
    groups     = get_active_groups()

    # Per-week group overrides
    group_cap_map = get_group_capacity_map(groups, labels)
    # Which (group_id, week) have explicit overrides
    overrides_set = {
        (r.group_id, r.week)
        for r in CustomerGroupCapacityTarget.query
        .filter(CustomerGroupCapacityTarget.week.in_(labels))
        .all()
    }

    today_iso = _week_label(date.today())
    rows = []
    for w in weeks:
        t = target_map[w["label"]]
        group_caps = {}
        for g in groups:
            cap = group_cap_map.get((g.id, w["label"]), float(g.weekly_capacity or 0))
            group_caps[g.id] = {
                "cap":         cap,
                "is_override": (g.id, w["label"]) in overrides_set,
            }
        rows.append({
            **w,
            "target_per_day": t["target_per_day"],
            "working_days":   t["working_days"],
            "weekly_sum":     t["target"],
            "is_override":    t["is_override"],
            "is_current":     w["label"] == today_iso,
            "group_caps":     group_caps,
        })
    return rows, groups


def bulk_save_capacity_targets(
    rows: list[dict],
    user_id: Optional[int],
) -> int:
    """
    Save a list of {week, target_per_day, working_days} rows.

    - Rows where tpd == 0 are skipped (deleted if an override exists).
    - Rows where values match the current default are also removed (no
      need to store a week override that duplicates the default).
    - Returns number of rows saved as explicit overrides.
    """
    from datetime import datetime, timezone as _tz

    default = get_default_target()
    saved = 0

    for row in rows:
        week = row.get("week", "").strip()
        if not week:
            continue
        try:
            tpd   = float(row.get("target_per_day", 0) or 0)
            wdays = int(row.get("working_days", 4)   or 4)
        except (TypeError, ValueError):
            continue

        existing = PlanningCapacityTarget.query.filter_by(week=week).first()

        # Only remove an override if values exactly match the rolling default
        # (blank inputs are already excluded upstream by the route; 0 is valid)
        matches_default = (
            default and
            float(default.target_per_day or 0) == tpd and
            int(default.working_days or 0) == wdays
        )
        if matches_default:
            if existing:
                db.session.delete(existing)
            continue

        if not existing:
            existing = PlanningCapacityTarget(week=week)
            db.session.add(existing)
        existing.target_per_day = tpd
        existing.working_days   = wdays
        existing.updated_by_id  = user_id
        existing.updated_at     = datetime.now(_tz.utc)
        saved += 1

    db.session.commit()
    return saved


# ---------------------------------------------------------------------------
# Customer group helpers
# ---------------------------------------------------------------------------

def get_all_groups() -> list[CustomerGroup]:
    return (
        CustomerGroup.query
        .order_by(CustomerGroup.sort_order.nulls_last(), CustomerGroup.name)
        .all()
    )


def get_active_groups() -> list[CustomerGroup]:
    return (
        CustomerGroup.query
        .filter_by(is_active=True)
        .order_by(CustomerGroup.sort_order.nulls_last(), CustomerGroup.name)
        .all()
    )


def create_group(name: str, colour: str, sort_order: Optional[int]) -> CustomerGroup:
    g = CustomerGroup(name=name, colour=colour or None, sort_order=sort_order)
    db.session.add(g)
    db.session.commit()
    return g


def update_group(
    group_id: int, name: str, colour: str,
    sort_order: Optional[int], is_active: bool,
    weekly_capacity: Optional[float] = None,
) -> CustomerGroup:
    g = CustomerGroup.query.get_or_404(group_id)
    g.name            = name
    g.colour          = colour or None
    g.sort_order      = sort_order
    g.is_active       = is_active
    g.weekly_capacity = weekly_capacity
    db.session.commit()
    return g


def save_group_capacity(group_id: int, weekly_capacity: Optional[float]) -> CustomerGroup:
    """Set the default weekly capacity for a customer group."""
    g = CustomerGroup.query.get_or_404(group_id)
    g.weekly_capacity = weekly_capacity
    db.session.commit()
    return g


def get_group_capacity_map(
    groups: list[CustomerGroup],
    week_labels: list[str],
) -> dict[tuple[int, str], float]:
    """
    Return effective capacity for each (group_id, week) pair.

    Lookup order: week-specific CustomerGroupCapacityTarget row →
                  CustomerGroup.weekly_capacity (the group default) → 0.
    """
    if not groups or not week_labels:
        return {}

    group_ids = [g.id for g in groups]
    overrides = (
        CustomerGroupCapacityTarget.query
        .filter(
            CustomerGroupCapacityTarget.group_id.in_(group_ids),
            CustomerGroupCapacityTarget.week.in_(week_labels),
        )
        .all()
    )
    override_map = {(r.group_id, r.week): float(r.weekly_capacity) for r in overrides}
    group_defaults = {g.id: float(g.weekly_capacity or 0) for g in groups}

    result = {}
    for g in groups:
        for wk in week_labels:
            result[(g.id, wk)] = override_map.get((g.id, wk), group_defaults[g.id])
    return result


def bulk_save_group_week_capacities(
    rows: list[dict],
    user_id: Optional[int],
) -> int:
    """
    Upsert per-week group capacity rows.

    Each row: {group_id, week, weekly_capacity}
    Rows with capacity == group default are deleted (no override needed).
    Rows with capacity == 0 are also deleted.
    Returns number of overrides saved.
    """
    saved = 0
    group_defaults = {
        g.id: float(g.weekly_capacity or 0)
        for g in CustomerGroup.query.all()
    }

    for row in rows:
        group_id = row.get("group_id")
        week     = (row.get("week") or "").strip()
        try:
            cap = float(row.get("weekly_capacity", 0) or 0)
        except (TypeError, ValueError):
            continue

        if not group_id or not week:
            continue

        existing = CustomerGroupCapacityTarget.query.filter_by(
            group_id=group_id, week=week
        ).first()

        default = group_defaults.get(group_id, 0)
        # Remove override only if value matches the group default exactly
        if cap == default:
            if existing:
                db.session.delete(existing)
            continue

        if not existing:
            existing = CustomerGroupCapacityTarget(group_id=group_id, week=week)
            db.session.add(existing)
        existing.weekly_capacity = cap
        saved += 1

    db.session.commit()
    return saved


def delete_group(group_id: int) -> None:
    g = CustomerGroup.query.get_or_404(group_id)
    db.session.delete(g)
    db.session.commit()


def set_group_members(group_id: int, customer_ids: list[str]) -> CustomerGroup:
    """
    Replace the full member list for a group.

    Removes any customer_ids already assigned to other groups if they
    appear in the new list (each customer can only be in one group).
    """
    g = CustomerGroup.query.get_or_404(group_id)
    clean_ids = [c.strip() for c in customer_ids if c.strip()]

    # Remove existing members that are no longer in the list
    existing = {m.customer_id: m for m in g.members}
    for cid, m in existing.items():
        if cid not in clean_ids:
            db.session.delete(m)

    # Add new members (remove from other groups first)
    for cid in clean_ids:
        if cid not in existing:
            # Evict from any other group, flush before inserting
            old = CustomerGroupMember.query.filter_by(customer_id=cid).first()
            if old:
                db.session.delete(old)
                db.session.flush()
            db.session.add(CustomerGroupMember(group_id=group_id, customer_id=cid))

    db.session.commit()
    return g


def add_group_member(group_id: int, customer_id: str) -> CustomerGroupMember:
    cid = customer_id.strip()
    # Evict from existing group if present, flush so the unique constraint is clear
    old = CustomerGroupMember.query.filter_by(customer_id=cid).first()
    if old:
        db.session.delete(old)
        db.session.flush()
    m = CustomerGroupMember(group_id=group_id, customer_id=cid)
    db.session.add(m)
    db.session.commit()
    return m


def remove_group_member(group_id: int, customer_id: str) -> None:
    CustomerGroupMember.query.filter_by(
        group_id=group_id, customer_id=customer_id.strip()
    ).delete()
    db.session.commit()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------



def _effective_plnwk(wo: WorksOrder, override_map: dict) -> str:
    """
    Return the effective ISO planned week for a job, applying any session override.

    Priority:
    1. Session override (stored in ISO format)
    2. Epicor prod_plnwk decoded from YYMMDD format
    3. Derived from req_due_date (used for Firm jobs that have no plnwk assigned yet)
    """
    ov = override_map.get((wo.job_num, wo.assembly_seq or 0))
    if ov and ov.override_plnwk:
        return ov.override_plnwk

    if wo.prod_plnwk:
        return _epicor_plnwk_to_iso(wo.prod_plnwk)

    # Firm jobs typically have no prod_plnwk; derive from due date so they appear in the plan
    if wo.req_due_date:
        return _week_label(wo.req_due_date)

    return ""


def _remaining_qty(wo: WorksOrder) -> float:
    """Units still to produce (floor 0)."""
    req  = float(wo.required_qty  or 0)
    done = float(wo.qty_completed or 0)
    return max(0.0, req - done)


def _load_value(wo: WorksOrder, dept: Department, measure: str) -> float:
    """
    Return the load contribution of this job in the requested measure.

    units  – remaining_qty  (default, always available)
    smv    – remaining_qty * dept.smv_per_unit  (falls back to units if not set)
    """
    remaining = _remaining_qty(wo)
    if measure == "smv":
        smv_rate = getattr(dept, "smv_per_unit", None)
        if smv_rate:
            return remaining * float(smv_rate)
    return remaining


def _dept_for_wo(wo: WorksOrder, dept_by_opcode: dict) -> Optional[Department]:
    """Map a WorksOrder to its Department via next_op → op_code."""
    if not wo.next_op:
        return None
    return dept_by_opcode.get(wo.next_op.strip().upper())


def _build_override_map(session: Optional[PlanningSession]) -> dict:
    if not session:
        return {}
    return {
        (ov.job_num, ov.assembly_seq or 0): ov
        for ov in session.overrides
    }


def _state_of(wo: WorksOrder) -> str:
    """Classify a WorksOrder into one of three active states."""
    if wo.job_released:
        return "released"   # Released = True, Complete = False (complete excluded upstream)
    if wo.job_firm:
        return "firm"       # Firm = True, Released = False
    return "unfirm"         # Firm = False, Released = False


def _cell_status(avail: float, load: float) -> str:
    if avail == 0:
        return "no-data"
    if load > avail:
        return "over"
    if load >= avail * 0.85:
        return "warning"
    return "ok"


# ---------------------------------------------------------------------------
# Base query helpers
# ---------------------------------------------------------------------------

def _all_active_wo_query():
    """
    All incomplete, named-model, non-scatter works orders.
    Used for capacity board calculations.
    """
    return (
        WorksOrder.query
        .filter(WorksOrder.job_complete.isnot(True))
        .filter(WorksOrder.model.isnot(None))
        .filter(WorksOrder.model != "")
        .filter(WorksOrder.model.notilike("%scatter%"))
    )


def _display_wo_query(state_filter: str):
    """
    ALL incomplete works orders for the detail display table.
    No model-type filter — shows scatter and no-model jobs too.
    State filter still applies.
    """
    q = WorksOrder.query.filter(WorksOrder.job_complete.isnot(True))
    if state_filter == FILTER_FIRM:
        q = q.filter(WorksOrder.job_firm.is_(True), WorksOrder.job_released.isnot(True))
    elif state_filter == FILTER_RELEASED:
        q = q.filter(WorksOrder.job_released.is_(True))
    elif state_filter == FILTER_UNFIRM:
        q = q.filter(WorksOrder.job_firm.isnot(True), WorksOrder.job_released.isnot(True))
    return q


# ---------------------------------------------------------------------------
# Main workspace builder
# ---------------------------------------------------------------------------

def get_planning_workspace(
    session_id: Optional[int],
    from_date: date,
    num_weeks: int = 13,
    state_filter: str = FILTER_ALL,
    dept_id: Optional[int] = None,
    measure: str = "units",
) -> dict:
    """
    Build the full dataset for the capacity planning workspace.

    Board columns: Unfirm | Firm | Released | Total vs Available
    Orders list: filtered by state_filter for the drill-down table.
    """
    session = PlanningSession.query.get(session_id) if session_id else None

    weeks       = _period_weeks(from_date, num_weeks)
    from_dt     = weeks[0][0]
    to_dt       = weeks[-1][1]
    week_labels = [w[2] for w in weeks]

    override_map = _build_override_map(session)

    # Departments (for dept filter on orders list and capacity lookup)
    all_departments = (
        Department.query
        .filter_by(is_active=True)
        .order_by(Department.flow_order.nulls_last(), Department.name)
        .all()
    )
    dept_by_opcode = {
        d.op_code.strip().upper(): d
        for d in all_departments
        if d.op_code
    }
    display_depts = (
        [d for d in all_departments if d.id == dept_id] if dept_id else all_departments
    )

    avail_map = get_available_by_week_dept(from_dt, to_dt)

    # -----------------------------------------------------------------------
    # Pass 1: ALL active orders → populate board status buckets + group buckets
    # -----------------------------------------------------------------------
    STATUS_KEYS = ("unfirm", "firm", "released")
    board_load_map:  dict[tuple[str, str], float] = {}
    board_count_map: dict[tuple[str, str], int]   = {}

    # Customer groups: build lookup customer_id → group
    active_groups    = get_active_groups()
    # Per-week group capacities (week-specific overrides → group default)
    group_cap_map    = get_group_capacity_map(active_groups, week_labels)
    cust_to_group_id = {
        m.customer_id.strip(): m.group_id
        for g in active_groups
        for m in g.members
    }
    group_id_to_obj = {g.id: g for g in active_groups}

    # (week_label, group_id, state) → {jobs, load}
    group_load_map:  dict[tuple[str, int, str], float] = {}
    group_count_map: dict[tuple[str, int, str], int]   = {}

    for wo in _all_active_wo_query().all():
        eff_wk = _effective_plnwk(wo, override_map)
        if eff_wk not in week_labels:
            continue
        dept = _dept_for_wo(wo, dept_by_opcode)
        if dept is None:
            continue
        if dept_id and dept.id != dept_id:
            continue

        state = _state_of(wo)
        load  = _load_value(wo, dept, measure)

        # Status bucket
        key = (eff_wk, state)
        board_load_map[key]  = board_load_map.get(key,  0.0) + load
        board_count_map[key] = board_count_map.get(key, 0)   + 1

        # Group bucket — ungrouped customers go into the "standard" bucket
        gid = cust_to_group_id.get((wo.customer_id or "").strip())
        if gid is not None:
            gkey = (eff_wk, gid, state)
            group_load_map[gkey]  = group_load_map.get(gkey,  0.0) + load
            group_count_map[gkey] = group_count_map.get(gkey, 0)   + 1
        else:
            # Standard (unassigned) bucket: key uses gid=0
            skey = (eff_wk, 0, state)
            group_load_map[skey]  = group_load_map.get(skey,  0.0) + load
            group_count_map[skey] = group_count_map.get(skey, 0)   + 1

    # -----------------------------------------------------------------------
    # Pass 2: ALL display orders (no model filter) → order-grouped detail table
    # -----------------------------------------------------------------------
    effective_orders = []
    today_date = date.today()
    _display_jobs = []

    for wo in _display_wo_query(state_filter).all():
        eff_wk = _effective_plnwk(wo, override_map)
        if eff_wk not in week_labels:
            continue
        dept = _dept_for_wo(wo, dept_by_opcode) if dept_id else None
        if dept_id and dept is None:
            continue

        load = _load_value(wo, dept, measure) if dept else _remaining_qty(wo)
        gid  = cust_to_group_id.get((wo.customer_id or "").strip())
        _display_jobs.append({
            "wo":              wo,
            "dept":            dept,
            "effective_plnwk": eff_wk,
            "is_overridden":   (wo.job_num, wo.assembly_seq or 0) in override_map,
            "remaining_qty":   _remaining_qty(wo),
            "load_value":      round(load, 2),
            "state":           _state_of(wo),
            "group_name":      group_id_to_obj[gid].name if gid else None,
        })

    # Fetch real fabric (material) status for all orders in one call
    _so_numbers = list({str(j["wo"].order_num) for j in _display_jobs if j["wo"].order_num})
    _so_fab_status: dict[str, str] = get_so_material_status(_so_numbers) if _so_numbers else {}

    # Enrich each display job with its fabric status
    for j in _display_jobs:
        so_key = str(j["wo"].order_num) if j["wo"].order_num else ""
        j["fab_status"] = _so_fab_status.get(so_key, "no_data")
        # Keep backward-compat mtl_status (boolean shortage) alongside fab_status
        j["mtl_status"] = "shortage" if j["wo"].mtl_shortage else "ok"
        effective_orders.append(j)

    # -----------------------------------------------------------------------
    # Pass 3: Overdue summary row
    # All active named non-scatter jobs with req_due_date < today,
    # regardless of planned week — a global "backlog" row on the board.
    # -----------------------------------------------------------------------
    od_status:  dict[str, dict] = {st: {"jobs": 0, "load": 0.0} for st in STATUS_KEYS}
    od_groups:  dict[int, dict] = {
        g.id: {st: {"jobs": 0, "load": 0.0} for st in STATUS_KEYS}
        for g in active_groups
    }
    od_total_jobs = 0
    od_total_load = 0.0

    for wo in _all_active_wo_query().filter(
        WorksOrder.req_due_date.isnot(None),
        WorksOrder.req_due_date < today_date,
    ).all():
        dept = _dept_for_wo(wo, dept_by_opcode)
        if dept is None:
            continue
        if dept_id and dept.id != dept_id:
            continue

        state = _state_of(wo)
        load  = _load_value(wo, dept, measure)
        od_status[state]["jobs"] += 1
        od_status[state]["load"]  = round(od_status[state]["load"] + load, 1)
        od_total_jobs += 1
        od_total_load  = round(od_total_load + load, 1)

        gid = cust_to_group_id.get((wo.customer_id or "").strip())
        if gid in od_groups:
            od_groups[gid][state]["jobs"] += 1
            od_groups[gid][state]["load"]  = round(od_groups[gid][state]["load"] + load, 1)
        else:
            # Standard overdue bucket (gid=None → key 0)
            if 0 not in od_groups:
                od_groups[0] = {st: {"jobs": 0, "load": 0.0} for st in STATUS_KEYS}
            od_groups[0][state]["jobs"] += 1
            od_groups[0][state]["load"]  = round(od_groups[0][state]["load"] + load, 1)

    # Build group_cells for overdue row
    od_group_cells = {}
    for g in active_groups:
        g_st  = od_groups.get(g.id, {})
        g_tot = round(sum(g_st.get(s, {}).get("load", 0) for s in STATUS_KEYS), 1)
        od_group_cells[g.id] = {
            "status_cells": {st: {"load": round(g_st.get(st, {}).get("load", 0), 1),
                                   "jobs": g_st.get(st, {}).get("jobs", 0)}
                              for st in STATUS_KEYS},
            "total_load": g_tot,
            "total_jobs": sum(g_st.get(s, {}).get("jobs", 0) for s in STATUS_KEYS),
            "capacity": 0, "util_pct": None, "cap_status": "no-data",
        }

    # Standard overdue cell
    std_od = od_groups.get(0, {})
    od_standard_cell = {
        "status_cells": {st: {"load": round(std_od.get(st, {}).get("load", 0), 1),
                               "jobs": std_od.get(st, {}).get("jobs", 0)}
                          for st in STATUS_KEYS},
        "total_load": round(sum(std_od.get(s, {}).get("load", 0) for s in STATUS_KEYS), 1),
        "total_jobs": sum(std_od.get(s, {}).get("jobs", 0) for s in STATUS_KEYS),
    }

    overdue_summary = {
        "status_cells":  od_status,
        "total_jobs":    od_total_jobs,
        "total_load":    od_total_load,
        "group_cells":   od_group_cells,
        "standard_cell": od_standard_cell,
    }

    # -----------------------------------------------------------------------
    # Capacity targets
    # -----------------------------------------------------------------------
    target_map = get_capacity_targets(week_labels)

    # -----------------------------------------------------------------------
    # Weekly board rows
    # -----------------------------------------------------------------------
    today_iso = _week_label(date.today())

    overdue_by_week:  dict[str, int] = {}
    shortage_by_week: dict[str, int] = {}
    for o in effective_orders:
        wo = o["wo"]
        if wo.req_due_date and wo.req_due_date < today_date:
            overdue_by_week[o["effective_plnwk"]] = overdue_by_week.get(o["effective_plnwk"], 0) + 1
        # Count any material risk (anything worse than ok/no_data)
        if o.get("fab_status", "no_data") not in ("ok", "no_data"):
            shortage_by_week[o["effective_plnwk"]] = shortage_by_week.get(o["effective_plnwk"], 0) + 1

    board_weeks = []
    for ws, we, label in weeks:
        status_cells = {}
        total_load   = 0.0
        total_jobs   = 0

        for st in STATUS_KEYS:
            key   = (label, st)
            load  = round(board_load_map.get(key, 0.0), 1)
            count = board_count_map.get(key, 0)
            status_cells[st] = {"jobs": count, "load": load}
            total_load += load
            total_jobs += count

        total_load   = round(total_load, 1)
        total_avail  = round(
            sum(avail_map.get((label, d.id), 0.0) for d in display_depts), 1
        )
        cap          = target_map[label]
        target_units = cap["target"]
        cap_is_set   = cap["is_set"]
        cap_status   = _cell_status(target_units, total_load) if target_units else _cell_status(total_avail, total_load)
        cap_util     = round(total_load / target_units * 100, 1) if target_units else (
            round(total_load / total_avail * 100, 1) if total_avail else None
        )

        # Per-group cells
        group_cells = {}
        for g in active_groups:
            g_cells = {}
            g_total = 0.0
            g_jobs  = 0
            for st in STATUS_KEYS:
                gkey  = (label, g.id, st)
                gload = round(group_load_map.get(gkey, 0.0), 1)
                gcnt  = group_count_map.get(gkey, 0)
                g_cells[st] = {"jobs": gcnt, "load": gload}
                g_total += gload
                g_jobs  += gcnt
            g_total   = round(g_total, 1)
            g_cap     = group_cap_map.get((g.id, label), float(g.weekly_capacity or 0))
            g_util    = round(g_total / g_cap * 100, 1) if g_cap else None
            g_status  = _cell_status(g_cap, g_total) if g_cap else "no-data"
            group_cells[g.id] = {
                "status_cells":  g_cells,
                "total_load":    g_total,
                "total_jobs":    g_jobs,
                "capacity":      g_cap,
                "util_pct":      g_util,
                "cap_status":    g_status,
                "pct_of_total":  round(g_total / total_load * 100, 1) if total_load else None,
            }

        # Standard bucket: ungrouped customers (gid=0)
        std_cells = {}
        std_total = 0.0
        std_jobs  = 0
        for st in STATUS_KEYS:
            skey   = (label, 0, st)
            sload  = round(group_load_map.get(skey, 0.0), 1)
            scnt   = group_count_map.get(skey, 0)
            std_cells[st] = {"jobs": scnt, "load": sload}
            std_total += sload
            std_jobs  += scnt
        std_total = round(std_total, 1)

        # Standard capacity = total week capacity minus all named group capacities
        named_cap_total = sum(
            group_cap_map.get((g.id, label), float(g.weekly_capacity or 0))
            for g in active_groups
        )
        std_cap   = max(0.0, round(target_units - named_cap_total, 1))
        std_util  = round(std_total / std_cap * 100, 1) if std_cap else None
        std_status = _cell_status(std_cap, std_total) if std_cap else "no-data"

        standard_cell = {
            "status_cells": std_cells,
            "total_load":   std_total,
            "total_jobs":   std_jobs,
            "capacity":     std_cap,
            "util_pct":     std_util,
            "cap_status":   std_status,
            "pct_of_total": round(std_total / total_load * 100, 1) if total_load else None,
        }

        board_weeks.append({
            "label":        label,
            "week_start":   ws,
            "week_end":     we,
            "date_range":   f"{ws.strftime('%d/%m')} – {we.strftime('%d/%m/%y')}",
            "week_num":     ws.isocalendar()[1],
            "is_current":   label == today_iso,
            "is_past":      we < today_date,
            "status_cells": status_cells,
            "total_jobs":   total_jobs,
            "total_load":   total_load,
            "total_avail":  total_avail,
            "target":       target_units,
            "target_is_set": cap_is_set,
            "target_max":   cap["max"],
            "cap_util":     cap_util,
            "cap_status":   cap_status,
            "group_cells":   group_cells,
            "standard_cell": standard_cell,
            "overdue":       overdue_by_week.get(label, 0),
            "shortages":    shortage_by_week.get(label, 0),
        })

    effective_orders.sort(key=lambda x: (
        x["effective_plnwk"] or "zzzz",
        x["dept"].flow_order if x["dept"] else 999,
        x["wo"].job_num or "",
    ))

    return {
        "session":        session,
        "weeks":          week_labels,
        "weeks_raw":      weeks,
        "display_depts":  display_depts,
        "board_weeks":    board_weeks,
        "overdue_summary": overdue_summary,
        "orders":         effective_orders,
        "override_map":   override_map,
        "has_capacity":   bool(avail_map),
        "active_groups":  active_groups,
        "state_filter":  state_filter,
        "measure":       measure,
        "from_date":     from_dt,
        "to_date":       to_dt,
    }


# ---------------------------------------------------------------------------
# Order-level grouping for the detail table
# ---------------------------------------------------------------------------

def group_orders_by_order_num(orders: list[dict]) -> list[dict]:
    """
    Group the flat orders list by order_num for the aggregated detail view.

    Returns a list sorted by (effective_plnwk, order_num), each entry:
      order_num, customer_name, model, jobs (list), total_remaining,
      total_load, min_due, states (set), primary_state, effective_plnwk,
      all_same_plnwk, is_overridden, has_shortage, group_name
    """
    from collections import defaultdict

    buckets: dict[int, list] = defaultdict(list)
    for o in orders:
        key = o["wo"].order_num or 0
        buckets[key].append(o)

    groups = []
    for order_num, jobs in buckets.items():
        wo0           = jobs[0]["wo"]
        plnwks        = {j["effective_plnwk"] for j in jobs}
        states        = {j["state"] for j in jobs}
        total_rem     = sum(j["remaining_qty"] for j in jobs)
        total_load    = sum(j["load_value"]    for j in jobs)
        dues          = [j["wo"].req_due_date for j in jobs if j["wo"].req_due_date]

        # Primary state: released > firm > unfirm
        if "released" in states:
            primary = "released"
        elif "firm" in states:
            primary = "firm"
        else:
            primary = "unfirm"

        eff_wk = jobs[0]["effective_plnwk"] if len(plnwks) == 1 else "mixed"

        groups.append({
            "order_num":      order_num,
            "customer_name":  wo0.customer_name or "—",
            "model":          wo0.model or "—",
            "part_num":       wo0.part_num or "",
            "jobs":           jobs,
            "job_count":      len(jobs),
            "total_remaining": round(total_rem, 0),
            "total_load":     round(total_load, 1),
            "min_due":        min(dues) if dues else None,
            "states":         states,
            "primary_state":  primary,
            "effective_plnwk": eff_wk,
            "all_same_plnwk": len(plnwks) == 1,
            "is_overridden":  any(j["is_overridden"] for j in jobs),
            "has_shortage":   any(j["mtl_status"] == "shortage" for j in jobs),
                "display_order":  bool(wo0.display_order),
                "fab_status":     max(
                    (j.get("fab_status", "no_data") for j in jobs),
                    key=lambda s: _MAT_STATUS_PRIORITY.get(s, -1),
                    default="no_data",
                ),
                "group_name":     jobs[0]["group_name"],
            })

    groups.sort(key=lambda x: (
        x["effective_plnwk"] or "zzzz",
        x["order_num"] or 0,
    ))
    return groups


# ---------------------------------------------------------------------------
# Order-level override (applies plnwk to ALL jobs in an order)
# ---------------------------------------------------------------------------

def upsert_order_override(
    session_id: int,
    order_num: int,
    override_plnwk: Optional[str],
    override_due_date,
    notes: Optional[str],
    user_id: int,
) -> int:
    """
    Apply a planning override to every WorksOrder row with the given order_num
    that exists in the active (non-scatter, named-model) query.
    Returns the number of job overrides created/updated.
    """
    jobs = (
        WorksOrder.query
        .filter(WorksOrder.job_complete.isnot(True))
        .filter(WorksOrder.order_num == order_num)
        .all()
    )
    count = 0
    for wo in jobs:
        upsert_override(
            session_id=session_id,
            job_num=wo.job_num,
            assembly_seq=wo.assembly_seq or 0,
            override_plnwk=override_plnwk,
            override_due_date=override_due_date,
            notes=notes,
            user_id=user_id,
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def list_sessions() -> list[PlanningSession]:
    return (
        PlanningSession.query
        .order_by(PlanningSession.created_at.desc())
        .all()
    )


def get_session_or_404(session_id: int) -> PlanningSession:
    return PlanningSession.query.get_or_404(session_id)


def create_session(name: str, description: str, user_id: int) -> PlanningSession:
    s = PlanningSession(name=name, description=description, created_by_id=user_id)
    db.session.add(s)
    db.session.commit()
    return s


def clone_session(source_id: int, new_name: str, user_id: int) -> PlanningSession:
    """Deep-copy a session and all its overrides."""
    source = PlanningSession.query.get_or_404(source_id)
    new_s  = PlanningSession(
        name=new_name,
        description=f"Clone of '{source.name}'",
        created_by_id=user_id,
    )
    db.session.add(new_s)
    db.session.flush()
    for ov in source.overrides:
        db.session.add(PlanningOverride(
            session_id=new_s.id,
            job_num=ov.job_num,
            assembly_seq=ov.assembly_seq,
            override_plnwk=ov.override_plnwk,
            override_due_date=ov.override_due_date,
            notes=ov.notes,
            created_by_id=user_id,
        ))
    db.session.commit()
    return new_s


def delete_session(session_id: int) -> None:
    s = PlanningSession.query.get_or_404(session_id)
    db.session.delete(s)
    db.session.commit()


# ---------------------------------------------------------------------------
# Override CRUD
# ---------------------------------------------------------------------------

def upsert_override(
    session_id: int,
    job_num: str,
    assembly_seq: int,
    override_plnwk: Optional[str],
    override_due_date,
    notes: Optional[str],
    user_id: int,
) -> PlanningOverride:
    ov = PlanningOverride.query.filter_by(
        session_id=session_id,
        job_num=job_num,
        assembly_seq=assembly_seq or 0,
    ).first()
    if not ov:
        ov = PlanningOverride(
            session_id=session_id,
            job_num=job_num,
            assembly_seq=assembly_seq or 0,
            created_by_id=user_id,
        )
        db.session.add(ov)
    ov.override_plnwk    = override_plnwk    or None
    ov.override_due_date = override_due_date or None
    ov.notes             = notes             or None
    db.session.commit()
    return ov


def remove_override(session_id: int, job_num: str, assembly_seq: int) -> None:
    PlanningOverride.query.filter_by(
        session_id=session_id,
        job_num=job_num,
        assembly_seq=assembly_seq or 0,
    ).delete()
    db.session.commit()


# ---------------------------------------------------------------------------
# DMT export (stub — column mapping to be provided by user)
# ---------------------------------------------------------------------------

def build_dmt_export_csv(session_id: int) -> str:
    """
    Return a CSV string of overridden jobs for Epicor DMT upload.

    Column names are provisional — the final mapping will be supplied separately.
    Only jobs with at least one non-null override field are included.
    """
    session = PlanningSession.query.get_or_404(session_id)

    fieldnames = [
        "JobNum", "AssemblySeq",
        "ProdPlnWk_Override", "ReqDueDate_Override",
        "CustomerID", "PartNum", "Description",
        "RemainingQty", "State", "Notes",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\r\n")
    writer.writeheader()

    for ov in session.overrides:
        wo: Optional[WorksOrder] = WorksOrder.query.filter_by(
            job_num=ov.job_num, assembly_seq=ov.assembly_seq or 0
        ).first()

        remaining = (
            max(0.0, float(wo.required_qty or 0) - float(wo.qty_completed or 0))
            if wo else 0
        )
        state = ""
        if wo:
            if wo.job_released:
                state = "RELEASED"
            elif wo.job_firm:
                state = "FIRM"

        writer.writerow({
            "JobNum":               ov.job_num,
            "AssemblySeq":          ov.assembly_seq or 0,
            "ProdPlnWk_Override":   ov.override_plnwk or "",
            "ReqDueDate_Override":  ov.override_due_date.isoformat() if ov.override_due_date else "",
            "CustomerID":           wo.customer_id if wo else "",
            "PartNum":              wo.part_num    if wo else "",
            "Description":          (wo.description or "")[:80] if wo else "",
            "RemainingQty":         f"{remaining:.1f}",
            "State":                state,
            "Notes":                ov.notes or "",
        })
    return buf.getvalue()
