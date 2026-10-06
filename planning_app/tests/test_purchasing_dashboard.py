from html.parser import HTMLParser

import pytest
from flask import url_for

from .conftest import login


class DashboardCardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_main = False
        self.in_heading = False
        self.href = None
        self.cards = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "main":
            self.in_main = True
        elif self.in_main and tag == "a":
            self.href = attributes.get("href")
        elif self.in_main and tag == "h6" and self.href:
            self.in_heading = True
            self.cards[self.href] = ""

    def handle_data(self, data):
        if self.in_heading:
            self.cards[self.href] += data

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = False
        elif tag == "a":
            self.href = None
        elif tag == "h6":
            self.in_heading = False


@pytest.mark.parametrize("path", ["/purchasing/", "/purchasing/dashboard"])
@pytest.mark.parametrize("user_fixture", ["admin_user", "viewer_user", "purchasing_user"])
def test_purchasing_dashboard_lists_modules(app, client, request, path, user_fixture):
    user = request.getfixturevalue(user_fixture)
    password = {
        "admin_user": "Admin!Pass1234",
        "viewer_user": "Viewer!Pass1234",
        "purchasing_user": "Purchasing!Pass1234",
    }[user_fixture]
    login(client, user.email, password)

    response = client.get(path)

    assert response.status_code == 200
    parser = DashboardCardParser()
    parser.feed(response.get_data(as_text=True))
    modules = {
        "purchasing.overview": "Overview",
        "materials.index": "Fabric/Hide Availability",
        "materials.shortage": "Fabric/Hide Shortages",
        "materials.component_shortage": "Component Shortages",
        "materials.mrp": "MRP Pegging",
        "materials.release_impact": "Cash Release Impact",
        "purchasing.supplier_delivery": "Supplier Delivery",
        "materials.stock_list": "Stock on Hand",
        "materials.po_list": "Open POs",
        "materials.main_requirements": "Material Reqs",
    }
    if user.has_permission("manage_imports") or user.has_permission("manage_purchasing"):
        modules.update({
            "materials.exempt_materials": "MRP Exemptions",
            "materials.class_labels": "Class Labels",
        })
    with app.test_request_context():
        expected_cards = {url_for(endpoint): label for endpoint, label in modules.items()}
    assert parser.cards == expected_cards
