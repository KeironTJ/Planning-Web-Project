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
    kpi_summary = {
        "total_orders":   len(orders_list),
        "ready_orders":   total_ready_orders,
        "partial_orders": total_partial_orders,
        "held_count":     len(_kpi_held),
        "held_value":     sum(o["invoiceable_value"] for o in _kpi_held),
        "urgent_count":   len(_kpi_urgent),
        "urgent_value":   sum(o["invoiceable_value"] for o in _kpi_urgent),
        "intl_count":     sum(1 for o in orders_list
                               if o["is_international"] and o["order_status"] == "ready"
                               and not o["on_hold"]),
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
    customer_summary = sorted(
        cust_map.values(), key=lambda x: x["value"], reverse=True
    )[:15]

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
        customer_summary=customer_summary,
        urgent_orders=urgent_orders,
        today=today,
        sort=sort,
        search=search,
        customer_f=customer_f,
        status_f=status_f,
    )
