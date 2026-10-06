from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from flask import url_for

from .conftest import login


class LandingLinksParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_main = False
        self.in_sidebar = False
        self.main_links = set()
        self.sidebar_links = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "main":
            self.in_main = True
        elif tag == "nav" and attributes.get("id") == "sidebarMenu":
            self.in_sidebar = True
        elif tag == "a" and attributes.get("href"):
            path = urlsplit(attributes["href"]).path
            if self.in_main:
                self.main_links.add(path)
            if self.in_sidebar:
                self.sidebar_links.add(path)

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = False
        elif tag == "nav":
            self.in_sidebar = False


SECTION_MODULES = {
    "sales.dashboard": {
        "orders.order_book_dashboard", "orders.order_book", "orders.overdue_report",
        "sales.customer_report", "sales.model_report",
    },
    "planning.dashboard": {
        "capacity.dashboard", "capacity.labour_plan_list", "workorder_plan.sessions",
        "workorder_plan.groups_list", "workorder_plan.capacity_targets",
    },
    "purchasing.dashboard": {
        "purchasing.overview", "materials.index", "materials.shortage",
        "materials.component_shortage", "materials.mrp", "materials.release_impact",
        "purchasing.supplier_delivery", "materials.stock_list", "materials.po_list",
        "materials.main_requirements", "materials.exempt_materials", "materials.class_labels",
    },
    "operations.dashboard": {"operations.wip_overview", "operations.daily_output"},
    "transport.dashboard": {
        "transport.loads", "transport.manifest_history",
        "transport.loading_bay", "transport.bay_state",
    },
    "admin.dashboard": {
        "admin.user_list", "admin.role_list", "admin.dept_list", "admin.epicor_sync",
        "admin.import_list", "admin.audit_log", "admin.system_settings",
        "admin.sync_schedules", "admin.data_main_material",
    },
    "it.dashboard": set(),
}


@pytest.mark.parametrize("endpoint", SECTION_MODULES)
def test_admin_landing_links_cover_section_modules(app, client, admin_user, endpoint):
    login(client, admin_user.email, "Admin!Pass1234")
    with app.test_request_context():
        path = url_for(endpoint)
        expected_paths = {url_for(module) for module in SECTION_MODULES[endpoint]}

    response = client.get(path)

    assert response.status_code == 200
    parser = LandingLinksParser()
    parser.feed(response.get_data(as_text=True))
    assert expected_paths <= parser.main_links
    if endpoint == "it.dashboard":
        assert response.get_data(as_text=True).count("Coming soon") == 2
    else:
        section_prefixes = {urlsplit(path).path.rsplit("/", 1)[0] for path in expected_paths}
        sidebar_modules = {
            path for path in parser.sidebar_links
            if path in expected_paths or any(
                path.startswith(prefix + "/") for prefix in section_prefixes
            )
        }
        sidebar_modules.discard(path)
        assert sidebar_modules <= parser.main_links


@pytest.mark.parametrize(
    ("user_fixture", "password", "endpoint", "hidden_endpoints"),
    [
        ("viewer_user", "Viewer!Pass1234", "planning.dashboard", {"capacity.labour_plan_list"}),
        ("planner_user", "Planner!Pass1234", "sales.dashboard",
         {"sales.customer_report", "sales.model_report"}),
        ("viewer_user", "Viewer!Pass1234", "purchasing.dashboard",
         {"materials.exempt_materials", "materials.class_labels"}),
    ],
)
def test_restricted_tools_hidden_on_landing_and_sidebar(
    app, client, request, user_fixture, password, endpoint, hidden_endpoints,
):
    user = request.getfixturevalue(user_fixture)
    login(client, user.email, password)
    with app.test_request_context():
        path = url_for(endpoint)
        hidden_paths = {url_for(module) for module in hidden_endpoints}
        expected_paths = {
            url_for(module) for module in SECTION_MODULES[endpoint] - hidden_endpoints
        }

    response = client.get(path)

    assert response.status_code == 200
    parser = LandingLinksParser()
    parser.feed(response.get_data(as_text=True))
    assert expected_paths <= parser.main_links
    assert hidden_paths.isdisjoint(parser.main_links)
    assert hidden_paths.isdisjoint(parser.sidebar_links)


def test_purchasing_manager_sees_management_tools_on_both_surfaces(app, client, purchasing_user):
    assert purchasing_user.has_permission("manage_purchasing")
    assert not purchasing_user.has_permission("manage_imports")
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    response = client.get("/purchasing/dashboard")

    assert response.status_code == 200
    parser = LandingLinksParser()
    parser.feed(response.get_data(as_text=True))
    with app.test_request_context():
        expected_paths = {
            url_for(module) for module in ("materials.exempt_materials", "materials.class_labels")
        }
    assert expected_paths <= parser.main_links
    assert expected_paths <= parser.sidebar_links
