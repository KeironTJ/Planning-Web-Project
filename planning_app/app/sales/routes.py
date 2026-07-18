"""Sales department portal routes."""

from datetime import date

from flask import render_template, request
from flask_login import login_required

from . import sales_bp
from app.core.decorators import permission_required


@sales_bp.route("/")
@sales_bp.route("/dashboard")
@login_required
@permission_required("view_orders")
def dashboard():
    return render_template("sales/dashboard.html", title="Sales")


@sales_bp.route("/customer-report")
@login_required
@permission_required("view_orders")
def customer_report():
    customer_ids  = [v.strip() for v in request.args.getlist("customer_id") if v.strip()]
    closed_months = request.args.get("closed_months", 12, type=int)
    if closed_months not in (3, 6, 12):
        closed_months = 12

    from app.sales.orders import services

    customers = services.get_customer_list()

    if not customer_ids:
        return render_template(
            "sales/customer_report.html",
            title="Customer Report",
            customers=customers,
            customer_ids=[],
            closed_months=closed_months,
            today=date.today(),
        )

    data = services.get_customer_report(customer_ids, closed_months)
    if data is None:
        return render_template(
            "sales/customer_report.html",
            title="Customer Report",
            customers=customers,
            customer_ids=customer_ids,
            closed_months=closed_months,
            today=date.today(),
        )

    comment_summaries = services.get_comment_summaries(
        [o["so_number"] for o in data["open_orders"]]
    )
    for o in data["open_orders"]:
        cs = comment_summaries.get(o["so_number"], {})
        o["comment_count"]  = cs.get("count", 0)
        o["latest_comment"] = cs.get("latest_body")
        o["latest_user"]    = cs.get("latest_user")
        o["latest_at"]      = cs.get("latest_at")

    title = (
        f"Customer Report \u2013 {data['customer_info']['customer_name']}"
        if not data["customer_info"]["is_combined"]
        else f"Customer Report \u2013 {data['customer_info']['customer_name']}"
    )
    return render_template(
        "sales/customer_report.html",
        title=title,
        customers=customers,
        customer_ids=customer_ids,
        closed_months=closed_months,
        today=date.today(),
        **data,
    )
