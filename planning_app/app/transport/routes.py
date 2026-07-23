"""Transport department portal routes."""

from collections import defaultdict
from datetime import date

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.sales.orders.models import SalesOrder
from . import transport_bp


@transport_bp.route("/")
@transport_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("transport/dashboard.html", title="Transport")


# ---------------------------------------------------------------------------
# Loading Bay Report
# ---------------------------------------------------------------------------

@transport_bp.route("/loading-bay")
@login_required
def loading_bay():
    """
    Finished-goods / Loading Bay report.

    Shows all open sales-order releases where the linked production job is
    complete (qty_completed >= required_qty).  Orders where *some* releases
    are still in WIP are flagged as partial so the transport team can see
    what they can and cannot invoice immediately.
    """
    today = date.today()

    # ── Filters ────────────────────────────────────────────────────────
    search        = request.args.get("q", "").strip()
    customer_f    = request.args.get("customer", "").strip()
    status_f      = request.args.get("status", "").strip()
    if status_f not in ("", "ready", "partial", "on_hold", "urgent"):
        status_f = ""
    sort          = request.args.get("sort", "due_date")
    if sort not in ("due_date", "customer", "value", "so_number"):
        sort = "due_date"

    # ── Base query: all open top-level assembly rows ────────────────────
    q = (
        db.session.query(SalesOrder)
        .filter(
            SalesOrder.open_order == True,       # noqa: E712
            SalesOrder.assembly_seq == 0,
        )
    )

    if search:
        term = f"%{search}%"
        q = q.filter(db.or_(
            func.cast(SalesOrder.order_num, db.String).ilike(term),
            SalesOrder.customer_name.ilike(term),
            SalesOrder.po_num.ilike(term),
            SalesOrder.part_num.ilike(term),
        ))

    if customer_f:
        q = q.filter(SalesOrder.customer_name.ilike(f"%{customer_f}%"))

    all_rows = (
        q.order_by(
            SalesOrder.need_by_date.asc().nullslast(),
            SalesOrder.order_num,
            SalesOrder.order_line,
            SalesOrder.rel_num,
        )
        .all()
    )

    # ── Build order → release → job hierarchy ──────────────────────────
    # order_num → header dict with releases sub-dict
    orders_map: dict[int, dict] = {}
    order_keys: list[int] = []          # preserves query order

    for row in all_rows:
        onum = row.order_num

        if onum not in orders_map:
            orders_map[onum] = {
                "order_num":            onum,
                "customer_id":          row.customer_id or "",
                "customer_name":        row.customer_name or "",
                "customer_country":     row.customer_country or "",
                "po_num":               row.po_num or "",
                "so_type_desc":         row.so_type_desc or "",
                "channel":              row.channel or "",
                "order_date":           row.order_date,
                "currency_code":        row.currency_code or "GBP",
                # Hold flags (order-level — same on every row for a given order)
                "so_credit_hold":       bool(row.so_credit_hold),
                "customer_credit_hold": bool(row.customer_credit_hold),
                "order_held":           bool(row.order_held),
                "releases":             {},
            }
            order_keys.append(onum)

        order = orders_map[onum]
        rkey  = (row.order_line, row.rel_num)

        if rkey not in order["releases"]:
            order["releases"][rkey] = {
                "order_line":        row.order_line,
                "rel_num":           row.rel_num,
                "part_num":          row.part_num or "",
                "part_desc":         row.part_desc or "",
                "model":             row.model or "",
                "size_desc":         row.size_desc or "",
                "prod_size":         row.prod_size or "",
                "cover":             row.cover or "",
                "cover_desc":        row.cover_desc or "",
                "selling_qty":       float(row.selling_qty or 0),
                "shipped_qty":       float(row.shipped_qty or 0),
                "required_qty":      float(row.required_qty or 0),
                "qty_completed":     float(row.qty_completed or 0),
                "release_price_gbp": float(row.release_price_gbp or 0),
                "need_by_date":      row.need_by_date,
                "wip_bin":           row.wip_bin or "",
                "jobs":              [],
            }
        else:
            # Multiple jobs linked to same release — take best completion
            rel = order["releases"][rkey]
            comp = float(row.qty_completed or 0)
            if comp > rel["qty_completed"]:
                rel["qty_completed"] = comp

        rel = order["releases"][rkey]
        if row.job_num:
            rel["jobs"].append({
                "job_num":       row.job_num,
                "qty_completed": float(row.qty_completed or 0),
                "required_qty":  float(row.required_qty or 0),
                "next_op":       "",   # filled below
            })

    # ── Fetch next_op for all tracked jobs from WorksOrder ─────────────
    from app.operations.models import WorksOrder as WO
    all_job_nums: set[str] = set()
    for onum in order_keys:
        for rel in orders_map[onum]["releases"].values():
            for j in rel["jobs"]:
                if j["job_num"]:
                    all_job_nums.add(j["job_num"])

    next_op_map: dict[str, str] = {}
    if all_job_nums:
        wo_rows = (
            db.session.query(WO.job_num, WO.next_op)
            .filter(WO.job_num.in_(list(all_job_nums)), WO.assembly_seq == 0)
            .all()
        )
        for wo in wo_rows:
            if wo.next_op:
                next_op_map[wo.job_num] = wo.next_op

    for onum in order_keys:
        for rel in orders_map[onum]["releases"].values():
            for j in rel["jobs"]:
                j["next_op"] = next_op_map.get(j["job_num"], "")

    # ── Classify releases & build display orders ────────────────────────
    _domestic = {"united kingdom", "uk", "gb", "great britain", "northern ireland"}

    orders_list: list[dict] = []
    total_invoiceable    = 0.0
    total_ready_orders   = 0
    total_partial_orders = 0
    total_units_ready    = 0.0

    for onum in order_keys:
        order    = orders_map[onum]
        releases = list(order["releases"].values())

        finished: list[dict] = []
        in_prog:  list[dict] = []
        not_started: list[dict] = []

        for rel in releases:
            req  = rel["required_qty"]
            comp = rel["qty_completed"]
            if req > 0 and comp >= req:
                rel["status"] = "finished"
                finished.append(rel)
            elif comp > 0:
                rel["status"] = "in_progress"
                in_prog.append(rel)
            else:
                rel["status"] = "not_started"
                not_started.append(rel)

        # Only include orders that have at least one finished release
        if not finished:
            continue

        # Order-level dates
        due_dates = [r["need_by_date"] for r in releases if r["need_by_date"]]
        order["due_date"]   = min(due_dates) if due_dates else None
        order["days_delta"] = (order["due_date"] - today).days if order["due_date"] else None

        # Value & qty totals
        order["invoiceable_value"] = sum(r["release_price_gbp"] for r in finished)
        order["total_value"]       = sum(r["release_price_gbp"] for r in releases)
        order["units_ready"]       = sum(r["qty_completed"]     for r in finished)
        order["units_total"]       = sum(r["selling_qty"]       for r in releases)
        order["finished_count"]    = len(finished)
        order["wip_count"]         = len(in_prog) + len(not_started)
        order["total_lines"]       = len(releases)

        # Hold & geography flags
        order["on_hold"] = (
            order["so_credit_hold"] or
            order["customer_credit_hold"] or
            order["order_held"]
        )
        order["is_international"] = bool(
            order["customer_country"] and
            order["customer_country"].lower() not in _domestic
        )

        # Status
        if not (in_prog or not_started):
            order["order_status"] = "ready"
            total_ready_orders   += 1
        else:
            order["order_status"] = "partial"
            total_partial_orders += 1

        # Sort releases: finished first, then in_progress, then not_started
        order["releases"] = finished + in_prog + not_started

        total_invoiceable += order["invoiceable_value"]
        total_units_ready += order["units_ready"]
        orders_list.append(order)

    # ── Snapshot unfiltered counts for KPI navigation cards ─────────────
    _kpi_held   = [o for o in orders_list if o["on_hold"] and o["order_status"] == "ready"]
    _kpi_urgent = [o for o in orders_list
                   if o["order_status"] == "ready" and not o["on_hold"]
                   and o["days_delta"] is not None and o["days_delta"] < 0]
    _kpi_ready  = [o for o in orders_list if o["order_status"] == "ready" and not o["on_hold"]]
    _kpi_partial = [o for o in orders_list if o["order_status"] == "partial"]
    kpi_summary = {
        "total_orders":    len(orders_list),
        "ready_orders":    total_ready_orders,
        "partial_orders":  total_partial_orders,
        "held_count":      len(_kpi_held),
        "held_value":      sum(o["invoiceable_value"] for o in _kpi_held),
        "urgent_count":    len(_kpi_urgent),
        "urgent_value":    sum(o["invoiceable_value"] for o in _kpi_urgent),
        "intl_count":      sum(1 for o in orders_list
                               if o["is_international"] and o["order_status"] == "ready"
                               and not o["on_hold"]),
        # Value breakdowns for KPI cards
        "ready_value":       sum(o["invoiceable_value"] for o in _kpi_ready),
        "partial_fin_value": sum(o["invoiceable_value"] for o in _kpi_partial),
        "total_value":       sum(o["invoiceable_value"] for o in orders_list),
        # Full potential: finished + all WIP lines if they were to complete
        "full_potential":    sum(o["total_value"] for o in orders_list),
    }

    # ── Shipping Horizon (unfiltered — ready orders by days to due) ──────
    _h_labels = ["Overdue", "Today", "1–3 days", "4–7 days", "8–14 days", "15+ days", "No date"]
    _h_ready  = [0.0] * 7
    _h_held   = [0.0] * 7

    for o in orders_list:
        if o["order_status"] != "ready":
            continue
        v = o["invoiceable_value"]
        d = o["days_delta"]
        if d is None:
            idx = 6
        elif d < 0:
            idx = 0
        elif d == 0:
            idx = 1
        elif d <= 3:
            idx = 2
        elif d <= 7:
            idx = 3
        elif d <= 14:
            idx = 4
        else:
            idx = 5
        if o["on_hold"]:
            _h_held[idx] += v
        else:
            _h_ready[idx] += v

    shipping_horizon = {
        "labels": _h_labels,
        "ready":  [round(v, 2) for v in _h_ready],
        "held":   [round(v, 2) for v in _h_held],
    }

    # ── Status filter (applied after classification) ────────────────────
    if status_f == "ready":
        orders_list = [o for o in orders_list if o["order_status"] == "ready" and not o["on_hold"]]
    elif status_f == "urgent":
        orders_list = [o for o in orders_list
                       if o["order_status"] == "ready" and not o["on_hold"]
                       and o["days_delta"] is not None and o["days_delta"] < 0]
    elif status_f == "partial":
        orders_list = [o for o in orders_list if o["order_status"] == "partial"]
    elif status_f == "on_hold":
        orders_list = [o for o in orders_list if o["on_hold"] and o["order_status"] == "ready"]

    # ── Urgent dispatch: ready + overdue + no hold ──────────────────────
    urgent_orders = sorted(
        [o for o in orders_list
         if o["order_status"] == "ready"
         and not o["on_hold"]
         and o["days_delta"] is not None
         and o["days_delta"] < 0],
        key=lambda o: o["days_delta"],   # most overdue first
    )

    # ── Apply sort ──────────────────────────────────────────────────────
    if sort == "customer":
        orders_list.sort(key=lambda o: (o["customer_name"], o["due_date"] or date.max))
    elif sort == "value":
        orders_list.sort(key=lambda o: o["invoiceable_value"], reverse=True)
    elif sort == "so_number":
        orders_list.sort(key=lambda o: o["order_num"])
    else:  # due_date default: urgent first, then ready by date, then partial
        orders_list.sort(key=lambda o: (
            0 if (o["order_status"] == "ready" and not o["on_hold"] and
                  o["days_delta"] is not None and o["days_delta"] < 0) else
            1 if (o["order_status"] == "ready" and not o["on_hold"]) else
            2 if o["order_status"] == "ready" else
            3,
            o["due_date"] or date.max,
        ))

    # ── Customer summary (for invoice potential bar chart) ─────────────
    cust_map: dict[str, dict] = {}
    for o in orders_list:
        cn = o["customer_name"] or "Unknown"
        if cn not in cust_map:
            cust_map[cn] = {
                "name":          cn,
                "value":         0.0,
                "units":         0.0,
                "units_total":   0.0,
                "orders":        0,
                "ready_value":   0.0,
                "partial_value": 0.0,
                "held_value":    0.0,
            }
        cust_map[cn]["value"]       += o["invoiceable_value"]
        cust_map[cn]["units"]       += o["units_ready"]
        cust_map[cn]["units_total"] += o["units_total"]
        cust_map[cn]["orders"] += 1
        if o["on_hold"]:
            cust_map[cn]["held_value"]    += o["invoiceable_value"]
        elif o["order_status"] == "ready":
            cust_map[cn]["ready_value"]   += o["invoiceable_value"]
        else:
            cust_map[cn]["partial_value"] += o["invoiceable_value"]
    _all_customers  = sorted(cust_map.values(), key=lambda x: x["value"], reverse=True)
    customer_chart = _all_customers[:15]          # chart — top 15 only
    customer_table = _all_customers               # full list for the summary table

    # ── Material shortage status for WIP orders ────────────────────────────
    try:
        from app.purchasing.materials.services import (
            get_so_material_status, get_job_material_status, MAT_STATUS_META,
        )
        _PRIO = {"no_data": -1, "ok": 0, "low_risk": 1, "med_risk": 2, "high_risk": 3}
        _so_strs  = [str(o["order_num"]) for o in orders_list]
        _mat_map  = get_so_material_status(_so_strs) if _so_strs else {}
        _all_jobs = [
            j["job_num"]
            for o in orders_list
            for rel in o["releases"]
            for j in rel["jobs"]
            if j["job_num"] and rel["status"] != "finished"
        ]
        _job_mat = get_job_material_status(_all_jobs) if _all_jobs else {}
        for o in orders_list:
            o["mat_status"] = _mat_map.get(str(o["order_num"]), "no_data")
            for rel in o["releases"]:
                if rel["status"] == "finished":
                    rel["mat_status"] = "no_data"
                else:
                    job_stats = [_job_mat.get(j["job_num"], "no_data") for j in rel["jobs"] if j["job_num"]]
                    rel["mat_status"] = max(job_stats, key=lambda s: _PRIO.get(s, -1)) if job_stats else "no_data"
    except Exception:
        MAT_STATUS_META = {}
        for o in orders_list:
            o["mat_status"] = "no_data"
            for rel in o["releases"]:
                rel["mat_status"] = "no_data"

    # ── Summary KPIs (all computed from filtered orders_list) ────────────
    held_ready   = [o for o in orders_list if o["on_hold"] and o["order_status"] == "ready"]
    ready_clear  = [o for o in orders_list if o["order_status"] == "ready" and not o["on_hold"]]
    partial_list = [o for o in orders_list if o["order_status"] == "partial"]
    summary = {
        # Filtered display values — update with status filter
        "total_orders":       len(orders_list),
        "ready_orders":       len(ready_clear),
        "partial_orders":     len(partial_list),
        "total_invoiceable":  sum(o["invoiceable_value"] for o in orders_list),
        "total_units_ready":  sum(o["units_ready"]       for o in orders_list),
        "held_count":         len(held_ready),
        "held_value":         sum(o["invoiceable_value"] for o in held_ready),
        "held_units":         sum(o["units_ready"]       for o in held_ready),
        "urgent_count":       len(urgent_orders),
        "urgent_value":       sum(o["invoiceable_value"] for o in urgent_orders),
        "intl_count":         sum(1 for o in orders_list
                                  if o["is_international"] and o["order_status"] == "ready"
                                  and not o["on_hold"]),
        # Chart breakdown (value & units per segment)
        "ready_clear_value":  sum(o["invoiceable_value"] for o in ready_clear),
        "partial_value":      sum(o["invoiceable_value"] for o in partial_list),
        "ready_clear_units":  sum(o["units_ready"]       for o in ready_clear),
        "partial_units":      sum(o["units_ready"]       for o in partial_list),
    }

    return render_template(
        "transport/loading_bay.html",
        title="Loading Bay Report",
        orders=orders_list,
        summary=summary,
        kpi_summary=kpi_summary,
        customer_chart=customer_chart,
        customer_table=customer_table,
        mat_status_meta=MAT_STATUS_META,
        urgent_orders=urgent_orders,
        shipping_horizon=shipping_horizon,
        today=today,
        sort=sort,
        search=search,
        customer_f=customer_f,
        status_f=status_f,
    )


# ---------------------------------------------------------------------------
# Loading Bay — Physical State
# ---------------------------------------------------------------------------

@transport_bp.route("/bay-state")
@login_required
def bay_state():
    """
    Physical loading bay state.

    Shows every finished-goods release grouped by its wip_bin location so
    the transport team can see what is physically staged and where.
    """
    today = date.today()
    _domestic = {"united kingdom", "uk", "gb", "great britain", "northern ireland"}

    rows = (
        db.session.query(SalesOrder)
        .filter(
            SalesOrder.open_order == True,   # noqa: E712
            SalesOrder.assembly_seq == 0,
            SalesOrder.required_qty > 0,
            SalesOrder.qty_completed >= SalesOrder.required_qty,
        )
        .order_by(
            SalesOrder.wip_bin,
            SalesOrder.need_by_date.asc().nullslast(),
            SalesOrder.order_num,
        )
        .all()
    )

    _bay_map: dict[str, list] = defaultdict(list)
    for row in rows:
        bin_name = (row.wip_bin or "").strip() or "Unstaged"
        on_hold  = bool(row.so_credit_hold or row.customer_credit_hold or row.order_held)
        days_delta = (row.need_by_date - today).days if row.need_by_date else None
        _bay_map[bin_name].append({
            "order_num":         row.order_num,
            "customer_name":     row.customer_name or "",
            "customer_country":  row.customer_country or "",
            "part_num":          row.part_num or "",
            "part_desc":         row.part_desc or "",
            "model":             row.model or "",
            "size_desc":         row.size_desc or "",
            "order_line":        row.order_line,
            "rel_num":           row.rel_num,
            "qty_completed":     float(row.qty_completed or 0),
            "release_price_gbp": float(row.release_price_gbp or 0),
            "need_by_date":      row.need_by_date,
            "days_delta":        days_delta,
            "on_hold":           on_hold,
            "is_international":  bool(
                row.customer_country and
                row.customer_country.lower() not in _domestic
            ),
        })

    bay_board = sorted(
        [
            {
                "bin":   k,
                "lines": sorted(v, key=lambda i: (i["need_by_date"] or date.max)),
                "value": round(sum(i["release_price_gbp"] for i in v), 2),
                "qty":   sum(i["qty_completed"] for i in v),
                "count": len(v),
            }
            for k, v in _bay_map.items()
        ],
        key=lambda b: ("zzzzz" if b["bin"] == "Unstaged" else b["bin"].lower()),
    )

    total_value = round(sum(b["value"] for b in bay_board), 2)
    total_qty   = sum(b["qty"]   for b in bay_board)
    total_lines = sum(b["count"] for b in bay_board)

    return render_template(
        "transport/bay_state.html",
        title="Loading Bay State",
        bay_board=bay_board,
        today=today,
        total_value=total_value,
        total_qty=total_qty,
        total_lines=total_lines,
    )
