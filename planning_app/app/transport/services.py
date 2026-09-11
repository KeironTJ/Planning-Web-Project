"""Business logic for transport reports."""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func

from app.extensions import db
from app.operations.models import WorksOrder
from app.sales.orders.models import SalesOrder
from app.purchasing.materials.services.status import (
    get_job_component_status,
    get_job_material_status,
    get_so_component_status,
    get_so_material_status,
)
from app.purchasing.materials.services.types import MAT_STATUS_META


_DOMESTIC_COUNTRIES = {
    "united kingdom", "uk", "gb", "great britain", "northern ireland",
}
_MATERIAL_STATUS_PRIORITY = {
    "no_data": -1, "ok": 0, "low_risk": 1, "med_risk": 2, "high_risk": 3,
}
_VALID_STATUSES = {"", "ready", "ready_hold", "partial", "partial_hold"}
_VALID_SORTS = {"due_date", "customer", "value", "so_number"}


def _is_on_hold(so_credit_hold: bool | None, order_held: bool | None) -> bool:
    return bool(so_credit_hold or order_held)


def get_loading_bay_report(
    search: str = "",
    customer: str = "",
    status: str = "",
    sort: str = "due_date",
    today: date | None = None,
) -> dict:
    """Build all data needed by the loading-bay report."""
    today = today or date.today()
    search = search.strip()
    customer = customer.strip()
    status = status if status in _VALID_STATUSES else ""
    sort = sort if sort in _VALID_SORTS else "due_date"

    query = db.session.query(SalesOrder).filter(
        SalesOrder.open_order == True,  # noqa: E712
        SalesOrder.assembly_seq == 0,
        SalesOrder.void_line.isnot(True),
    )
    if search:
        term = f"%{search}%"
        query = query.filter(db.or_(
            func.cast(SalesOrder.order_num, db.String).ilike(term),
            SalesOrder.customer_name.ilike(term),
            SalesOrder.po_num.ilike(term),
            SalesOrder.part_num.ilike(term),
        ))
    if customer:
        query = query.filter(SalesOrder.customer_name.ilike(f"%{customer}%"))

    rows = query.order_by(
        SalesOrder.need_by_date.asc().nullslast(),
        SalesOrder.order_num,
        SalesOrder.order_line,
        SalesOrder.rel_num,
    ).all()
    orders, order_keys = _build_orders(rows)
    _populate_job_statuses(orders, order_keys)
    orders_list = _classify_orders(orders, order_keys, today)
    kpi_summary = _loading_bay_kpis(orders_list)
    shipping_horizon = _shipping_horizon(orders_list)
    weekly_loading = _weekly_loading(orders_list, today)
    orders_list = _filter_and_sort_orders(orders_list, status, sort)
    customer_chart, customer_table = _customer_summary(orders_list)
    _populate_material_statuses(orders_list)
    summary = _filtered_summary(orders_list)

    return {
        "orders": orders_list,
        "summary": summary,
        "kpi_summary": kpi_summary,
        "customer_chart": customer_chart,
        "customer_table": customer_table,
        "mat_status_meta": MAT_STATUS_META,
        "shipping_horizon": shipping_horizon,
        "weekly_loading": weekly_loading,
        "today": today,
        "sort": sort,
        "search": search,
        "customer_f": customer,
        "status_f": status,
    }


def _build_orders(rows: list[SalesOrder]) -> tuple[dict[int, dict], list[int]]:
    orders: dict[int, dict] = {}
    order_keys: list[int] = []
    for row in rows:
        if row.order_num not in orders:
            orders[row.order_num] = {
                "order_num": row.order_num, "customer_id": row.customer_id or "",
                "customer_name": row.customer_name or "",
                "customer_country": row.customer_country or "", "po_num": row.po_num or "",
                "so_type_desc": row.so_type_desc or "", "channel": row.channel or "",
                "order_date": row.order_date, "currency_code": row.currency_code or "GBP",
                "so_credit_hold": bool(row.so_credit_hold),
                "customer_credit_hold": bool(row.customer_credit_hold),
                "order_held": bool(row.order_held), "releases": {},
            }
            order_keys.append(row.order_num)
        order = orders[row.order_num]
        release_key = (row.order_line, row.rel_num)
        if release_key not in order["releases"]:
            order["releases"][release_key] = {
                "order_line": row.order_line, "rel_num": row.rel_num,
                "part_num": row.part_num or "", "part_desc": row.part_desc or "",
                "model": row.model or "", "size_desc": row.size_desc or "",
                "prod_size": row.prod_size or "", "cover": row.cover or "",
                "cover_desc": row.cover_desc or "", "selling_qty": float(row.selling_qty or 0),
                "shipped_qty": float(row.shipped_qty or 0),
                "required_qty": float(row.required_qty or 0),
                "qty_completed": float(row.qty_completed or 0),
                "release_price_gbp": float(row.release_price_gbp or 0),
                "need_by_date": row.need_by_date, "wip_bin": row.wip_bin or "", "jobs": [],
            }
        else:
            release = order["releases"][release_key]
            release["qty_completed"] += float(row.qty_completed or 0)
            release["required_qty"] += float(row.required_qty or 0)
        if row.job_num:
            order["releases"][release_key]["jobs"].append({
                "job_num": row.job_num, "qty_completed": float(row.qty_completed or 0),
                "required_qty": float(row.required_qty or 0), "next_op": "",
            })
    return orders, order_keys


def _populate_job_statuses(orders: dict[int, dict], order_keys: list[int]) -> None:
    job_numbers = {
        job["job_num"] for key in order_keys for release in orders[key]["releases"].values()
        for job in release["jobs"] if job["job_num"]
    }
    if not job_numbers:
        return
    works_orders = db.session.query(
        WorksOrder.job_num, WorksOrder.next_op, WorksOrder.job_complete,
    ).filter(
        WorksOrder.job_num.in_(list(job_numbers)), WorksOrder.assembly_seq == 0,
    ).all()
    job_data = {
        work_order.job_num: (work_order.next_op or "", bool(work_order.job_complete))
        for work_order in works_orders
    }
    for key in order_keys:
        for release in orders[key]["releases"].values():
            for job in release["jobs"]:
                job["next_op"], job["job_complete"] = job_data.get(job["job_num"], ("", False))


def _classify_orders(orders: dict[int, dict], order_keys: list[int], today: date) -> list[dict]:
    result = []
    for key in order_keys:
        order = orders[key]
        releases = list(order["releases"].values())
        finished, in_progress, not_started = [], [], []
        for release in releases:
            jobs = release["jobs"]
            release["is_shipped"] = release["selling_qty"] > 0 and release["shipped_qty"] >= release["selling_qty"]
            release["is_partial_shipped"] = 0 < release["shipped_qty"] < release["selling_qty"]
            if not jobs:
                status = "finished" if release["required_qty"] > 0 and release["qty_completed"] >= release["required_qty"] else (
                    "in_progress" if release["qty_completed"] > 0 else "not_started"
                )
            elif (
                release["required_qty"] > 0
                and release["qty_completed"] >= release["required_qty"]
            ) or all(job["job_complete"] for job in jobs):
                status = "finished"
            elif any(job["job_complete"] or job["qty_completed"] > 0 for job in jobs):
                status = "in_progress"
            else:
                status = "not_started"
            release["status"] = status
            {"finished": finished, "in_progress": in_progress, "not_started": not_started}[status].append(release)
        bay_releases = [release for release in releases if release["wip_bin"].strip()]
        if not bay_releases:
            continue
        due_dates = [release["need_by_date"] for release in releases if release["need_by_date"]]
        order["due_date"] = min(due_dates) if due_dates else None
        order["days_delta"] = (order["due_date"] - today).days if order["due_date"] else None
        order["invoiceable_value"] = sum(release["release_price_gbp"] for release in bay_releases)
        order["total_value"] = sum(release["release_price_gbp"] for release in releases if not release["is_shipped"])
        order["units_ready"] = sum(release["qty_completed"] for release in finished)
        order["units_total"] = sum(release["selling_qty"] for release in releases)
        order["finished_count"], order["wip_count"], order["total_lines"] = (
            len(finished), len(in_progress) + len(not_started), len(releases)
        )
        order["on_hold"] = _is_on_hold(order["so_credit_hold"], order["order_held"])
        order["is_international"] = bool(
            order["customer_country"] and order["customer_country"].lower() not in _DOMESTIC_COUNTRIES
        )
        order["order_status"] = "ready" if not (in_progress or not_started) else "partial"
        order["releases"] = finished + in_progress + not_started
        result.append(order)
    return result


def _loading_bay_kpis(orders: list[dict]) -> dict:
    groups = _order_groups(orders)
    return {
        "total_orders": len(orders), **{f"{key}_count": len(value) for key, value in groups.items()},
        "intl_count": sum(order["is_international"] for order in groups["ready"]),
        **{f"{key}_value": sum(order["invoiceable_value"] for order in value) for key, value in groups.items()},
        "total_value": sum(order["invoiceable_value"] for order in orders),
        "full_potential": sum(order["total_value"] for order in orders),
    }


def _order_groups(orders: list[dict]) -> dict[str, list[dict]]:
    return {
        "ready": [order for order in orders if order["order_status"] == "ready" and not order["on_hold"]],
        "ready_hold": [order for order in orders if order["order_status"] == "ready" and order["on_hold"]],
        "partial": [order for order in orders if order["order_status"] == "partial" and not order["on_hold"]],
        "partial_hold": [order for order in orders if order["order_status"] == "partial" and order["on_hold"]],
    }


def _shipping_horizon(orders: list[dict]) -> dict:
    labels = ["Overdue", "Today", "1–3 days", "4–7 days", "8–14 days", "15+ days", "No date"]
    ready, ready_hold = [0.0] * 7, [0.0] * 7
    for order in orders:
        if order["order_status"] != "ready":
            continue
        days = order["days_delta"]
        index = 6 if days is None else 0 if days < 0 else 1 if days == 0 else 2 if days <= 3 else 3 if days <= 7 else 4 if days <= 14 else 5
        (ready_hold if order["on_hold"] else ready)[index] += order["invoiceable_value"]
    return {"labels": labels, "ready": [round(value, 2) for value in ready], "ready_hold": [round(value, 2) for value in ready_hold]}


def _weekly_loading(orders: list[dict], today: date) -> dict:
    week_start = today - timedelta(days=today.weekday())
    weeks = [(week_start + timedelta(weeks=index), week_start + timedelta(weeks=index, days=6)) for index in range(6)]
    labels = ["Overdue"] + [
        f"Wk {start.isocalendar().week}" if start.year == today.year else f"Wk {start.isocalendar().week} '{str(start.year)[-2:]}"
        for start, _ in weeks
    ] + ["Later / No Date"]
    values = {key: [0.0] * len(labels) for key in ("ready", "ready_hold", "partial", "partial_hold")}
    for order in orders:
        due_date = order["due_date"]
        index = len(labels) - 1 if due_date is None else 0 if due_date < week_start else next(
            (index + 1 for index, (start, end) in enumerate(weeks) if start <= due_date <= end),
            len(labels) - 1,
        )
        key = "ready_hold" if order["on_hold"] and order["order_status"] == "ready" else "partial_hold" if order["on_hold"] else order["order_status"]
        values[key][index] += order["invoiceable_value"]
    return {"labels": labels, **{key: [round(value, 2) for value in data] for key, data in values.items()}}


def _filter_and_sort_orders(orders: list[dict], status: str, sort: str) -> list[dict]:
    if status:
        orders = _order_groups(orders)[status]
    if sort == "customer":
        return sorted(orders, key=lambda order: (order["customer_name"], order["due_date"] or date.max))
    if sort == "value":
        return sorted(orders, key=lambda order: order["invoiceable_value"], reverse=True)
    if sort == "so_number":
        return sorted(orders, key=lambda order: order["order_num"])
    return sorted(orders, key=lambda order: (
        ("ready", "ready_hold", "partial", "partial_hold").index(
            "ready_hold" if order["on_hold"] and order["order_status"] == "ready"
            else "partial_hold" if order["on_hold"] else order["order_status"]
        ),
        order["due_date"] or date.max,
    ))


def _customer_summary(orders: list[dict]) -> tuple[list[dict], list[dict]]:
    customers: dict[str, dict] = {}
    for order in orders:
        name = order["customer_name"] or "Unknown"
        customer = customers.setdefault(name, {
            "name": name, "value": 0.0, "units": 0.0, "units_total": 0.0, "orders": 0,
            "ready_value": 0.0, "ready_hold_value": 0.0, "partial_value": 0.0, "partial_hold_value": 0.0,
        })
        customer["value"] += order["invoiceable_value"]
        customer["units"] += order["units_ready"]
        customer["units_total"] += order["units_total"]
        customer["orders"] += 1
        key = "ready_hold" if order["on_hold"] and order["order_status"] == "ready" else "partial_hold" if order["on_hold"] else order["order_status"]
        customer[f"{key}_value"] += order["invoiceable_value"]
    table = sorted(customers.values(), key=lambda customer: customer["value"], reverse=True)
    return table[:15], table


def _populate_material_statuses(orders: list[dict]) -> None:
    order_numbers = [str(order["order_num"]) for order in orders]
    so_material = get_so_material_status(order_numbers) if order_numbers else {}
    so_component = get_so_component_status(order_numbers) if order_numbers else {}
    job_numbers = [
        job["job_num"] for order in orders for release in order["releases"]
        if release["status"] != "finished" for job in release["jobs"] if job["job_num"]
    ]
    job_material = get_job_material_status(job_numbers) if job_numbers else {}
    job_component = get_job_component_status(job_numbers) if job_numbers else {}
    for order in orders:
        order["mat_status"] = so_material.get(str(order["order_num"]), "no_data")
        order["comp_status"] = so_component.get(str(order["order_num"]), "no_data")
        for release in order["releases"]:
            if release["status"] == "finished":
                release["mat_status"] = release["comp_status"] = "no_data"
                continue
            job_statuses = [job_material.get(job["job_num"], "no_data") for job in release["jobs"] if job["job_num"]]
            component_statuses = [job_component.get(job["job_num"], "no_data") for job in release["jobs"] if job["job_num"]]
            release["mat_status"] = max(
                job_statuses,
                key=lambda value: _MATERIAL_STATUS_PRIORITY.get(value, -1),
            ) if job_statuses else "no_data"
            release["comp_status"] = max(
                component_statuses,
                key=lambda value: _MATERIAL_STATUS_PRIORITY.get(value, -1),
            ) if component_statuses else "no_data"


def _filtered_summary(orders: list[dict]) -> dict:
    groups = _order_groups(orders)
    return {
        "total_orders": len(orders), **{f"{key}_count": len(value) for key, value in groups.items()},
        "total_invoiceable": sum(order["invoiceable_value"] for order in orders),
        "total_units_ready": sum(order["units_ready"] for order in orders),
        **{f"{key}_value": sum(order["invoiceable_value"] for order in value) for key, value in groups.items()},
        **{f"{key}_units": sum(order["units_ready"] for order in value) for key, value in groups.items()},
    }


def get_loading_bay_state(today: date | None = None) -> dict:
    """Build the physical loading-bay state grouped by staging location."""
    today = today or date.today()
    rows = db.session.query(SalesOrder).filter(
        SalesOrder.open_order == True,  # noqa: E712
        SalesOrder.assembly_seq == 0,
        SalesOrder.required_qty > 0,
        SalesOrder.qty_completed >= SalesOrder.required_qty,
    ).order_by(
        SalesOrder.wip_bin, SalesOrder.need_by_date.asc().nullslast(), SalesOrder.order_num,
    ).all()
    bays: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        bin_name = (row.wip_bin or "").strip() or "Unstaged"
        bays[bin_name].append({
            "order_num": row.order_num, "customer_name": row.customer_name or "",
            "customer_country": row.customer_country or "", "part_num": row.part_num or "",
            "part_desc": row.part_desc or "", "model": row.model or "", "size_desc": row.size_desc or "",
            "order_line": row.order_line, "rel_num": row.rel_num,
            "qty_completed": float(row.qty_completed or 0),
            "release_price_gbp": float(row.release_price_gbp or 0),
            "need_by_date": row.need_by_date,
            "days_delta": (row.need_by_date - today).days if row.need_by_date else None,
            "on_hold": _is_on_hold(row.so_credit_hold, row.order_held),
            "is_international": bool(row.customer_country and row.customer_country.lower() not in _DOMESTIC_COUNTRIES),
        })
    bay_board = sorted((
        {
            "bin": bin_name, "lines": sorted(lines, key=lambda line: line["need_by_date"] or date.max),
            "value": round(sum(line["release_price_gbp"] for line in lines), 2),
            "qty": sum(line["qty_completed"] for line in lines), "count": len(lines),
        }
        for bin_name, lines in bays.items()
    ), key=lambda bay: "zzzzz" if bay["bin"] == "Unstaged" else bay["bin"].lower())
    return {
        "bay_board": bay_board, "today": today,
        "total_value": round(sum(bay["value"] for bay in bay_board), 2),
        "total_qty": sum(bay["qty"] for bay in bay_board),
        "total_lines": sum(bay["count"] for bay in bay_board),
    }
