from dataclasses import asdict
from datetime import date, timedelta
from html.parser import HTMLParser
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import template_rendered

from app.extensions import db as _db
from app.purchasing.materials.services.netting import get_shortage_report
from .conftest import login
from .test_materials import make_po, make_req, make_stock


@pytest.fixture
def shortage_data(db):
    due = date.today() + timedelta(days=30)
    for index, group in enumerate(("fabric", "component"), 1):
        make_req(
            f"{group}-SHORT", qty_for_order=10, material_group=group,
            class_id="A101" if group == "fabric" else "COMP",
            works_order=f"{group}-JOB", so_number="1001", due_date=due,
        )
        make_req(
            f"{group}-PO", qty_for_order=10, material_group=group,
            class_id="A101" if group == "fabric" else "COMP",
            works_order=f"{group}-PO-JOB", so_number="1002",
            due_date=due, job_released=True,
        )
        make_po(f"{group}-PO", outstanding_qty=10, po_num=index)
        make_req(f"{group}-OK", material_group=group, qty_for_order=10)
        make_stock(f"{group}-OK", qty_on_hand=10)
    _db.session.commit()


@pytest.fixture
def shortage_context(app, client, purchasing_user):
    contexts = []

    def record(sender, template, context, **extra):
        if template.name == "materials/shortage.html":
            contexts.append(context)

    login(client, purchasing_user.email, "Purchasing!Pass1234")
    template_rendered.connect(record, app)
    yield contexts
    template_rendered.disconnect(record, app)


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.table = None
        self.counts = {}
        self.in_row = False
        self.headers = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        elif tag == "table":
            self.table = attrs.get("id")
            self.counts[self.table] = []
            self.headers[self.table] = 0
        elif tag == "tr" and self.table:
            self.in_row = True
            self.counts[self.table].append(0)
        elif tag in ("th", "td") and self.table and self.in_row:
            self.counts[self.table][-1] += 1
            if tag == "th":
                self.headers[self.table] += 1

    def handle_endtag(self, tag):
        if tag == "table":
            self.table = None
        elif tag == "tr":
            self.in_row = False


@pytest.mark.parametrize("group", ["fabric", "component", "all"])
def test_unified_shortages_match_existing_netting(app, client, shortage_context, shortage_data, group):
    with app.test_request_context():
        groups = ("fabric", "component") if group == "all" else (group,)
        expected = [
            asdict(row)
            for selected in groups
            for row in get_shortage_report(material_group=selected, shortages_only=False)["rows"]
            if row.status not in {"ok", "no_data"}
        ]
    response = client.get("/purchasing/materials/shortage", query_string={"material_group": group})
    assert response.status_code == 200
    context = shortage_context[-1]
    assert sorted((asdict(row) for row in context["data"]["rows"]), key=lambda row: row["material_code"]) == sorted(
        expected, key=lambda row: row["material_code"],
    )
    assert context["status_counts_all"] == {
        "high_risk": len(groups), "med_risk": len(groups),
    }
    assert context["shortage_insights"]["status_counts"] == context["status_counts_all"]
    assert context["data"]["total_rows"] == 2 * len(groups)
    assert {row.material_group for row in context["data"]["rows"]} == set(groups)
    parser = ReportParser()
    parser.feed(response.get_data(as_text=True))
    for table in ("summaryTable", "detailTable"):
        assert all(count == parser.headers[table] for count in parser.counts[table])
    assert b"material_group=" + group.encode() in response.data
    assert (b"Material Type" in response.data) == (group == "all")


def test_default_is_fabric(client, shortage_context, shortage_data):
    assert client.get("/purchasing/materials/shortage").status_code == 200
    assert shortage_context[-1]["material_group"] == "fabric"
    assert {row.material_group for row in shortage_context[-1]["data"]["rows"]} == {"fabric"}


@pytest.mark.parametrize("query, codes", [
    ({"so": "1001"}, {"fabric-SHORT", "component-SHORT"}),
    ({"status": "med_risk"}, {"fabric-PO", "component-PO"}),
    ({"cls": "COMP"}, {"component-SHORT", "component-PO"}),
    ({"wo_status": "released"}, {"fabric-PO", "component-PO"}),
    ({"q": "component-SHORT"}, {"component-SHORT"}),
    ({"so": "1001", "status": "med_risk"}, set()),
])
def test_combined_filters_and_insights_stay_in_sync(client, shortage_context, shortage_data, query, codes):
    response = client.get("/purchasing/materials/shortage", query_string={"material_group": "all", **query})
    assert response.status_code == 200
    context = shortage_context[-1]
    assert {row.material_code for row in context["data"]["rows"]} == codes
    assert sum(context["shortage_insights"]["status_counts"].values()) == len(codes)


def test_so_filter_does_not_change_cumulative_stock_allocation(client, shortage_context, db):
    due = date.today() + timedelta(days=30)
    make_req("SHARED", qty_for_order=10, so_number="1001", due_date=due)
    make_req("SHARED", qty_for_order=10, so_number="1002", due_date=due + timedelta(days=1))
    make_stock("SHARED", qty_on_hand=10)
    _db.session.commit()
    response = client.get("/purchasing/materials/shortage", query_string={
        "so": "1002", "due_from": (due + timedelta(days=1)).isoformat(),
    })
    assert response.status_code == 200
    row, = shortage_context[-1]["data"]["rows"]
    assert row.so_number == "1002"
    assert row.stock_on_hand == 0
    assert row.shortage == 10


def test_legacy_component_url_preserves_filters(client, shortage_context, shortage_data):
    query = {"so": "1001", "status": "high_risk", "cls": "COMP", "q": "SHORT", "material_group": "fabric"}
    response = client.get("/purchasing/materials/component-shortage", query_string=query)
    assert response.status_code == 302
    parsed = urlsplit(response.location)
    assert parsed.path == "/purchasing/materials/shortage"
    assert parse_qs(parsed.query) == {**{key: [value] for key, value in query.items()}, "material_group": ["component"]}
    response = client.get(response.location)
    assert response.status_code == 200
    assert [row.material_code for row in shortage_context[-1]["data"]["rows"]] == ["component-SHORT"]


def test_links_preserve_group_so_and_other_filters(client, shortage_context, shortage_data):
    response = client.get("/purchasing/materials/shortage", query_string={
        "material_group": "component", "so": "1001", "q": "SHORT", "cls": "COMP",
    })
    assert response.status_code == 200
    parser = ReportParser()
    html = response.get_data(as_text=True)
    parser.feed(html)
    queries = [
        parse_qs(urlsplit(link).query) for link in parser.links
        if urlsplit(link).path == "/purchasing/materials/shortage"
    ]
    fabric_switch = next(query for query in queries if query.get("material_group") == ["fabric"])
    assert fabric_switch["so"] == ["1001"]
    assert fabric_switch["q"] == ["SHORT"]
    assert "cls" not in fabric_switch
    kpi = next(query for query in queries if query.get("status") == ["med_risk"])
    assert kpi["material_group"] == ["component"] and kpi["so"] == ["1001"]
    search_clear = next(query for query in queries if query.get("cls") == ["COMP"] and "q" not in query)
    assert search_clear["material_group"] == ["component"] and search_clear["so"] == ["1001"]
    chart_data = html.split("classFilterUrl: ", 1)[1].split(",\n", 1)[0]
    chart_query = parse_qs(urlsplit(json.loads(chart_data)).query)
    assert chart_query["material_group"] == ["component"] and chart_query["so"] == ["1001"]
    mrp = next(
        link for link in parser.links
        if urlsplit(link).path == "/purchasing/materials/mrp"
        and parse_qs(urlsplit(link).query).get("q") == ["component-SHORT"]
    )
    assert parse_qs(urlsplit(mrp).query)["material_group"] == ["component"]


def test_empty_component_and_all_scopes_explain_missing_data(client, shortage_context):
    for group in ("component", "all"):
        response = client.get("/purchasing/materials/shortage", query_string={"material_group": group})
        assert response.status_code == 200
        assert b"No component requirement data found" in response.data
        assert b"component_requirements" in response.data
        if group == "all":
            assert b"No fabric and hide requirement data found" in response.data
    assert client.get("/purchasing/materials/shortage?material_group=invalid").status_code == 400
