"""
Orders blueprint routes.

Sales order review: open order book, overdue report, order comments.
"""

from datetime import date

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from . import orders_bp
from . import services
from app.core.decorators import permission_required


def _parse_date(val: str):
    """Parse a YYYY-MM-DD string from a query param; return None if blank/invalid."""
    if not val:
        return None
    try:
        return date.fromisoformat(val)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@orders_bp.route("/dashboard")
@login_required
@permission_required("view_orders")
def order_book_dashboard():
    data = services.get_order_book_dashboard()
    return render_template(
        "orders/dashboard.html",
        title="Orders Dashboard",
        **data,
    )


# ---------------------------------------------------------------------------
# Open Order Book
# ---------------------------------------------------------------------------

@orders_bp.route("/")
@orders_bp.route("/order-book")
@login_required
@permission_required("view_orders")
def order_book():
    search                = request.args.get("q", "")
    order_type_filter     = request.args.get("order_type", "")
    customer_filter       = request.args.get("customer", "")
    customer_po_filter    = request.args.get("cpo", "")
    country_filter        = request.args.get("country", "")
    customer_group_filter = request.args.get("cgroup", "")
    overdue_only          = request.args.get("overdue", "") == "1"
    order_by              = request.args.get("sort", "due_date")
    page                  = request.args.get("page", 1, type=int)
    per_page              = request.args.get("per_page", 25, type=int)
    due_date_from         = _parse_date(request.args.get("due_from", ""))
    due_date_to           = _parse_date(request.args.get("due_to", ""))

    if per_page not in (25, 50, 100):
        per_page = 25
    if order_by not in ("due_date", "so_number", "customer", "value"):
        order_by = "due_date"

    pagination, orders = services.get_order_book(
        page=page,
        per_page=per_page,
        search=search or None,
        order_type_filter=order_type_filter or None,
        customer_filter=customer_filter or None,
        customer_po_filter=customer_po_filter or None,
        country_filter=country_filter or None,
        customer_group_filter=customer_group_filter or None,
        overdue_only=overdue_only,
        order_by=order_by,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
    )
    summary         = services.get_order_book_summary()
    order_types     = services.get_order_types()
    customer_groups = services.get_customer_groups()
    countries       = services.get_countries()

    comment_summaries = services.get_comment_summaries([o["so_number"] for o in orders])
    for o in orders:
        cs = comment_summaries.get(o["so_number"], {})
        o["comment_count"]  = cs.get("count", 0)
        o["latest_comment"] = cs.get("latest_body")
        o["latest_user"]    = cs.get("latest_user")
        o["latest_at"]      = cs.get("latest_at")

    return render_template(
        "orders/order_book.html",
        title="Open Order Book",
        pagination=pagination,
        orders=orders,
        summary=summary,
        order_types=order_types,
        customer_groups=customer_groups,
        countries=countries,
        search=search,
        order_type_filter=order_type_filter,
        customer_filter=customer_filter,
        customer_po_filter=customer_po_filter,
        country_filter=country_filter,
        customer_group_filter=customer_group_filter,
        overdue_only=overdue_only,
        order_by=order_by,
        per_page=per_page,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        today=date.today(),
    )


# ---------------------------------------------------------------------------
# Overdue report
# ---------------------------------------------------------------------------

@orders_bp.route("/overdue")
@login_required
@permission_required("view_orders")
def overdue_report():
    search          = request.args.get("q", "")
    customer_filter = request.args.get("customer", "")
    page            = request.args.get("page", 1, type=int)
    per_page        = request.args.get("per_page", 50, type=int)
    sort            = request.args.get("sort", "days_overdue")

    if per_page not in (25, 50, 100):
        per_page = 50
    if sort not in ("days_overdue", "due_date", "value", "customer", "so_number"):
        sort = "days_overdue"

    data = services.get_overdue_orders(
        customer_filter=customer_filter or None,
        search=search or None,
        page=page,
        per_page=per_page,
        sort=sort,
    )

    comment_summaries = services.get_comment_summaries([o["so_number"] for o in data["orders"]])
    for o in data["orders"]:
        cs = comment_summaries.get(o["so_number"], {})
        o["comment_count"]  = cs.get("count", 0)
        o["latest_comment"] = cs.get("latest_body")
        o["latest_user"]    = cs.get("latest_user")
        o["latest_at"]      = cs.get("latest_at")

    return render_template(
        "orders/overdue_report.html",
        title="Overdue Orders",
        search=search,
        customer_filter=customer_filter,
        per_page=per_page,
        sort=sort,
        today=date.today(),
        **data,
    )


# ---------------------------------------------------------------------------
# Sales Order Comments (AJAX)
# ---------------------------------------------------------------------------

@orders_bp.route("/so/<so_number>/comments", methods=["GET"])
@login_required
@permission_required("view_orders")
def so_comments(so_number: str):
    """Return comments for an SO as JSON."""
    comments = services.get_so_comments(so_number)
    return jsonify({
        "ok": True,
        "comments": [
            {
                "id":         c.id,
                "user":       c.user.username if c.user else "deleted",
                "body":       c.body,
                "created_at": c.created_at.strftime("%d %b %Y %H:%M"),
            }
            for c in comments
        ],
    })


@orders_bp.route("/so/<so_number>/comments", methods=["POST"])
@login_required
@permission_required("update_order_status")
def add_so_comment(so_number: str):
    """Append a comment to an SO."""
    body = request.form.get("body", "").strip()
    try:
        comment = services.add_so_comment(so_number, current_user.id, body)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({
        "ok": True,
        "comment": {
            "id":         comment.id,
            "user":       current_user.username,
            "body":       comment.body,
            "created_at": comment.created_at.strftime("%d %b %Y %H:%M"),
        },
    })
