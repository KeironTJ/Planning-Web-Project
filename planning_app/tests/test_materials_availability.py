from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import template_rendered

from app.extensions import db as _db
from app.purchasing.materials.services.dashboard import (
    get_customer_coverage_summary, get_weekly_so_breakdown,
)
from app.sales.orders.models import SalesOrder
from .conftest import login
from .test_materials import make_po, make_req, make_stock, zero_lead_days


@pytest.fixture
def coverage_orders(db, zero_lead_days):
    from app.admin.models import SystemSetting

    SystemSetting.set("mrp_component_lead_days", "0")
    today = date.today()
    due = today + timedelta(days=30)
    overdue = today - timedelta(days=14)
    for number in range(1001, 1007):
        _db.session.add(SalesOrder(
            order_num=number, order_line=1, rel_num=1, assembly_seq=0,
            open_order=True, selling_qty=1, shipped_qty=0,
            req_date=overdue if number == 1003 else due,
            release_price_gbp=100,
            customer_id="C001" if number <= 1003 else "C002" if number <= 1005 else None,
            customer_name="Shared Customer Name" if number <= 1005 else None,
        ))
    # Duplicate job rows must not inflate SO count or release value.
    _db.session.add(SalesOrder(
        order_num=1001, order_line=1, rel_num=1, assembly_seq=1,
        open_order=True, selling_qty=1, shipped_qty=0,
        req_date=due, release_price_gbp=100,
    ))
    for number, kwargs in [
        (2001, {"shipped_qty": 1}),
        (2002, {"open_order": False}),
        (2003, {"req_date": today + timedelta(weeks=20)}),
    ]:
        values = dict(open_order=True, selling_qty=1, shipped_qty=0, req_date=due)
        values.update(kwargs)
        _db.session.add(SalesOrder(
            order_num=number, order_line=1, rel_num=1, release_price_gbp=999, **values,
        ))

    for number, fabric_status, component_status in [
        (1001, "ok", "high_risk"),
        (1002, "high_risk", "ok"),
        (1003, "ok", "ok"),
        (1004, "ok", "med_risk"),
        (1005, "late_po", "ok"),
    ]:
        for group, status in [("fabric", fabric_status), ("component", component_status)]:
            code = f"{number}-{group}"
            requirement_due = overdue if number == 1003 else due
            make_req(
                code, qty_for_order=10, so_number=str(number),
                material_group=group, due_date=requirement_due,
            )
            make_stock(code, qty_on_hand=10 if status == "ok" else 0)
            if status in {"med_risk", "late_po"}:
                make_po(
                    code, outstanding_qty=10, po_num=number,
                    due_date=due - timedelta(days=1) if status == "med_risk" else due + timedelta(days=1),
                )
    _db.session.commit()


@pytest.mark.parametrize("group, expected", [
    ("fabric", {"1001": "ok", "1002": "high_risk", "1003": "ok", "1004": "ok", "1005": "late_po", "1006": "ok"}),
    ("component", {"1001": "high_risk", "1002": "ok", "1003": "ok", "1004": "med_risk", "1005": "ok", "1006": "ok"}),
    ("all", {"1001": "high_risk", "1002": "high_risk", "1003": "ok", "1004": "med_risk", "1005": "late_po", "1006": "ok"}),
])
def test_breakdown_scopes_match_orders_and_values(app, coverage_orders, group, expected):
    with app.test_request_context():
        data = get_weekly_so_breakdown(material_group=group)

    assert {order["so_number"]: order["status"] for order in data["orders"]} == expected
    assert data["total_count"] == 6
    assert data["total_value"] == Decimal(600)
    for status, bucket in data["totals"].items():
        matching = [order for order in data["orders"] if order["status"] == status]
        assert bucket == {"count": len(matching), "value": Decimal(100) * len(matching)}
    for week in data["weeks"]:
        for status in data["totals"]:
            matching = [
                order for order in data["orders"]
                if order["iso_key"] == week["iso_key"] and order["status"] == status
            ]
            assert week[status]["count"] == len(matching)
            assert week[status]["value"] == sum(order["value"] for order in matching)
    assert data["weeks"][0]["is_overdue"]
    assert data["orders"][0]["so_number"] == "1003"


@pytest.fixture
def rendered_contexts(app):
    contexts = []

    def record(sender, template, context, **extra):
        if template.name == "materials/index.html":
            contexts.append(context)

    template_rendered.connect(record, app)
    yield contexts
    template_rendered.disconnect(record, app)


def test_default_and_card_drilldown(client, purchasing_user, coverage_orders, rendered_contexts):
    from html.parser import HTMLParser

    class LinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.links.append(dict(attrs).get("href", ""))

    login(client, purchasing_user.email, "Purchasing!Pass1234")
    response = client.get("/purchasing/materials/")
    assert response.status_code == 200
    assert rendered_contexts[-1]["material_group"] == "fabric"
    assert rendered_contexts[-1]["so_breakdown"]["totals"]["ok"]["count"] == 4
    parser = LinkParser()
    parser.feed(response.get_data(as_text=True))
    card_link = next(
        link for link in parser.links
        if urlsplit(link).fragment == "salesOrders"
        and parse_qs(urlsplit(link).query).get("status") == ["ok"]
    )
    response = client.get(card_link)
    assert response.status_code == 200
    assert {order["so_number"] for order in rendered_contexts[-1]["orders"]} == {
        "1001", "1003", "1004", "1006",
    }
    assert b"/purchasing/materials/mrp?so=1001&amp;material_group=fabric" in response.data


@pytest.mark.parametrize("group, status, expected", [
    ("component", "ok", {"1002", "1003", "1005", "1006"}),
    ("all", "ok", {"1003", "1006"}),
    ("all", "high_risk", {"1001", "1002"}),
    ("all", "med_risk", {"1004"}),
])
def test_status_drilldown_preserves_scope(client, purchasing_user, coverage_orders, rendered_contexts, group, status, expected):
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    response = client.get("/purchasing/materials/", query_string={"material_group": group, "status": status})
    assert response.status_code == 200
    context = rendered_contexts[-1]
    assert context["material_group"] == group
    assert {order["so_number"] for order in context["orders"]} == expected
    assert context["so_breakdown"]["total_count"] == 6


def test_overdue_drilldown_matches_week_exactly(client, purchasing_user, coverage_orders, rendered_contexts):
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    response = client.get("/purchasing/materials/?material_group=all&status=ok&week=0000-W00")
    assert response.status_code == 200
    assert [order["so_number"] for order in rendered_contexts[-1]["orders"]] == ["1003"]
    response = client.get("/purchasing/materials/?material_group=all&status=high_risk&week=0000-W00")
    assert response.status_code == 200
    assert rendered_contexts[-1]["orders"] == []
    assert b"No sales orders match these filters." in response.data


@pytest.mark.parametrize("query", ["material_group=invalid", "status=no_data", "week=invalid"])
def test_invalid_filters_are_rejected(client, purchasing_user, query):
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    assert client.get(f"/purchasing/materials/?{query}").status_code == 400


def test_empty_breakdown_and_page(app, client, purchasing_user, rendered_contexts):
    with app.test_request_context():
        for group in ("fabric", "component", "all"):
            data = get_weekly_so_breakdown(material_group=group)
            assert data["orders"] == data["weeks"] == []
            assert data["has_data"] is False
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    response = client.get("/purchasing/materials/?material_group=component")
    assert response.status_code == 200
    assert b"Components" in response.data
    assert b"No sales orders match these filters." in response.data


@pytest.mark.parametrize("group", ["fabric", "component", "all"])
def test_customer_summary_reconciles_to_coverage(app, coverage_orders, group):
    with app.test_request_context():
        data = get_weekly_so_breakdown(material_group=group)
    customers = get_customer_coverage_summary(data["orders"])
    by_code = {customer["customer_code"]: customer for customer in customers}
    assert set(by_code) == {"C001", "C002", ""}
    assert by_code["C001"]["customer_name"] == by_code["C002"]["customer_name"] == "Shared Customer Name"
    assert by_code["C001"]["total_count"] == 3
    assert by_code["C001"]["total_value"] == Decimal(300)
    assert by_code["C002"]["total_count"] == 2
    assert by_code[""]["total_count"] == 1
    assert sum(customer["total_count"] for customer in customers) == data["total_count"]
    assert sum(customer["total_value"] for customer in customers) == data["total_value"]
    for status in data["totals"]:
        assert sum(customer["statuses"][status]["count"] for customer in customers) == data["totals"][status]["count"]
        assert sum(customer["statuses"][status]["value"] for customer in customers) == data["totals"][status]["value"]
    assert get_customer_coverage_summary([]) == []
    assert [customer["total_value"] for customer in customers] == sorted(
        (customer["total_value"] for customer in customers), reverse=True,
    )


def test_customer_fields_and_order_book_link(client, purchasing_user, coverage_orders, rendered_contexts):
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    response = client.get("/purchasing/materials/?material_group=all")
    assert response.status_code == 200
    assert b"Customer Number" in response.data
    assert b"Customer Coverage Summary" in response.data
    assert response.data.index(b'id="coverageOrdersTable"') < response.data.index(b'id="customerCoverageTable"')
    assert b'aria-sort="descending"' in response.data
    assert b"Unassigned" in response.data
    assert b"/sales/orders/order-book?q=1001" in response.data or b"/sales/orders/?q=1001" in response.data
    context = rendered_contexts[-1]
    order = next(order for order in context["orders"] if order["so_number"] == "1001")
    assert order["customer_code"] == "C001"
    assert order["customer_name"] == "Shared Customer Name"
    assert len(context["customer_summary"]) == 3
    assert context["customer_options"] == [
        ("C001", "Shared Customer Name"), ("C002", "Shared Customer Name"),
    ]


@pytest.mark.parametrize("query, expected", [
    ({"customer": "C001"}, {"1001", "1002", "1003"}),
    ({"customer": "C001", "status": "ok"}, {"1003"}),
    ({"customer": "C001", "week": "0000-W00"}, {"1003"}),
    ({"customer": "C002", "status": "med_risk"}, {"1004"}),
    ({"customer": "C002", "week": "0000-W00"}, set()),
])
def test_customer_filters_apply_to_orders_and_summary(client, purchasing_user, coverage_orders, rendered_contexts, query, expected):
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    response = client.get("/purchasing/materials/", query_string={"material_group": "all", **query})
    assert response.status_code == 200
    context = rendered_contexts[-1]
    assert {order["so_number"] for order in context["orders"]} == expected
    assert sum(customer["total_count"] for customer in context["customer_summary"]) == len(expected)
    assert sum(customer["total_value"] for customer in context["customer_summary"]) == Decimal(100) * len(expected)
    assert context["so_breakdown"]["total_count"] == 6
    assert context["customer_filter"] == query["customer"]


@pytest.mark.parametrize("group", ["fabric", "component", "all"])
def test_material_availability_title_for_every_scope(client, purchasing_user, group, rendered_contexts):
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    response = client.get("/purchasing/materials/", query_string={"material_group": group})
    assert response.status_code == 200
    assert rendered_contexts[-1]["title"] == "Material Availability"
    assert b"<title>Material Availability" in response.data
    assert b"Fabric/Hide Availability" not in response.data
