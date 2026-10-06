from html.parser import HTMLParser

import pytest
from flask import render_template, url_for


class SidebarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.menus = {}
        self.toggles = {}
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "div" and attributes.get("id"):
            self.menus[attributes["id"]] = attributes.get("class", "").split()
        elif tag == "button" and attributes.get("aria-controls"):
            self.toggles[attributes["aria-controls"]] = attributes
        elif tag == "a":
            self.links.append(attributes.get("href"))


@pytest.mark.parametrize(
    ("endpoint", "menu_id"),
    [
        ("sales.dashboard", "salesMenu"),
        ("planning.dashboard", "planningMenu"),
        ("purchasing.dashboard", "purchasingMenu"),
        ("operations.dashboard", "operationsMenu"),
        ("transport.dashboard", "transportMenu"),
        ("admin.dashboard", "administrationMenu"),
        ("capacity.dashboard", "planningMenu"),
        ("purchasing.overview", "purchasingMenu"),
        ("materials.stock_list", "purchasingMenu"),
    ],
)
def test_sidebar_opens_current_section(app, admin_user, endpoint, menu_id):
    with app.test_request_context():
        path = url_for(endpoint)

    with app.test_request_context(path):
        sidebar = render_template(
            "components/sidebar.html",
            current_user=admin_user,
            active_departments=[],
        )

    parser = SidebarParser()
    parser.feed(sidebar)

    assert "show" in parser.menus[menu_id]
    assert parser.toggles[menu_id]["aria-expanded"] == "true"
    assert parser.toggles[menu_id]["data-bs-toggle"] == "collapse"
    assert parser.toggles[menu_id]["data-bs-target"] == f"#{menu_id}"
    assert path in parser.links
    for other_menu in ("salesMenu", "planningMenu", "purchasingMenu",
                       "operationsMenu", "transportMenu", "administrationMenu"):
        if other_menu != menu_id:
            assert "show" not in parser.menus[other_menu]
            assert parser.toggles[other_menu]["aria-expanded"] == "false"
