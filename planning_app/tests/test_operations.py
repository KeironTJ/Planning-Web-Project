"""Tests for Operations WIP ordering and availability filters."""

import csv
import io
from datetime import date
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from werkzeug.exceptions import BadRequest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.operations.models import WorksOrder
from app.operations.services import _quick_win_jobs, _wip_job_ordering, get_wip_export, get_wip_overview
from app.purchasing.materials.services.types import MAT_STATUS_META
from app.sales.orders.models import Department
from .conftest import login


def test_wip_jobs_are_ordered_by_due_date_sequence_order_and_job(app):
    with app.app_context():
        common = {"assembly_seq": 0, "job_released": True, "job_complete": False}
        db.session.add_all([
            WorksOrder(
                job_num="B-20", order_num=200, order_sort=20, next_op="UPH",
                req_due_date=date(2026, 9, 14), **common
            ),
            WorksOrder(
                job_num="B-10", order_num=200, order_sort=10, next_op="FRM",
                req_due_date=date(2026, 9, 14), **common
            ),
            WorksOrder(
                job_num="A-20", order_num=100, order_sort=20, next_op="UPH",
                req_due_date=date(2026, 9, 14), **common
            ),
            WorksOrder(
                job_num="A-10", order_num=100, order_sort=10, next_op="FRM",
                req_due_date=date(2026, 9, 14), **common
            ),
            WorksOrder(
                job_num="C-10", order_num=300, order_sort=30, next_op="FRM",
                req_due_date=date(2026, 9, 7), **common
            ),
        ])
        db.session.commit()

        jobs = WorksOrder.query.order_by(*_wip_job_ordering()).all()

        assert [(job.order_num, job.job_num) for job in jobs] == [
            (300, "C-10"),
            (100, "A-10"),
            (200, "B-10"),
            (100, "A-20"),
            (200, "B-20"),
        ]


def test_quick_wins_require_uphol_as_earliest_operation_and_no_shortage():
    completed = SimpleNamespace(order_num=100, job_complete=True, next_op=None)
    uphol = SimpleNamespace(order_num=100, job_complete=False, next_op="UPHOL")
    later = SimpleNamespace(order_num=100, job_complete=False, next_op="SEW")
    finished = SimpleNamespace(order_num=100, job_complete=False, next_op="FINISH")
    single = SimpleNamespace(order_num=400, job_complete=False, next_op="UPHOL")
    beyond = SimpleNamespace(order_num=500, job_complete=False, next_op="FINISH")
    earlier = SimpleNamespace(order_num=200, job_complete=False, next_op="FRAME")
    blocked = SimpleNamespace(order_num=300, job_complete=False, next_op="UPHOL")

    jobs = _quick_win_jobs(
        [completed, uphol, later, finished, single, beyond, earlier, blocked],
        {100, 200, 300},
        {400, 500},
        {"100": "ok", "200": "no_data", "300": "high_risk"},
        {"100": "no_data", "200": "no_data", "300": "ok", "400": "ok", "500": "ok"},
        {"FRAME": 1, "UPHOL": 2, "SEW": 3, "FINISH": 4},
    )

    assert jobs == [uphol, later, finished, single, beyond]


def test_wip_overview_filters_jobs_and_pivot_by_plan_week(app):
    with app.app_context():
        common = {
            "assembly_seq": 0,
            "job_released": True,
            "job_complete": False,
            "model": "MODEL",
            "next_op": "UPH",
            "required_qty": 1,
        }
        db.session.add_all([
            WorksOrder(job_num="W10", prod_plnwk="2610", **common),
            WorksOrder(job_num="W11", prod_plnwk="2611", **common),
        ])
        db.session.commit()

        result = get_wip_overview(MultiDict([("plan_week", "2610")]))

        assert [job.job_num for job in result["jobs"].items] == ["W10"]
        assert result["total"] == 1
        assert result["wip_weeks"] == ["2610"]
        assert result["plan_week"] == "2610"
        assert result["plan_week_options"] == ["2610", "2611"]


def test_wip_overview_filters_plan_sequence_within_week(app):
    with app.app_context():
        common = {
            "assembly_seq": 0,
            "job_released": True,
            "job_complete": False,
            "model": "MODEL",
            "next_op": "UPH",
            "required_qty": 1,
        }
        db.session.add_all([
            WorksOrder(job_num="W10A", prod_plnwk="261001", **common),
            WorksOrder(job_num="W10B", prod_plnwk="261002", **common),
        ])
        db.session.commit()

        result = get_wip_overview(MultiDict([
            ("plan_week", "2610"),
            ("plan_sequence", "01"),
        ]))

        assert [job.job_num for job in result["jobs"].items] == ["W10A"]
        assert result["plan_sequence_options"] == ["01", "02"]
        assert result["plan_sequences_by_week"] == {"2610": ["01", "02"]}


@pytest.fixture
def availability_jobs(app, monkeypatch):
    statuses = list(MAT_STATUS_META)
    material_map = {str(index + 1): status for index, status in enumerate(statuses)}
    component_map = {str(index + 1): "ok" for index in range(len(statuses))}
    component_map["1"] = "high_risk"
    component_map["6"] = "no_data"
    monkeypatch.setattr("app.operations.services.get_so_material_status", lambda orders: material_map.copy())
    monkeypatch.setattr("app.operations.services.get_so_component_status", lambda orders: component_map.copy())
    with app.app_context():
        common = dict(
            assembly_seq=0, job_released=True, job_complete=False,
            model="MODEL", next_op="UPH", required_qty=2, prod_plnwk="261001",
        )
        db.session.add_all([
            WorksOrder(job_num=f"STATUS-{index + 1}", order_num=index + 1, **common)
            for index in range(len(statuses))
        ])
        db.session.add_all([
            WorksOrder(job_num="MISSING", order_num=99, **common),
            WorksOrder(job_num="NO-ORDER", order_num=None, **common),
        ])
        db.session.commit()


@pytest.mark.parametrize("field", ["fabric_status", "component_status"])
@pytest.mark.parametrize("status", list(MAT_STATUS_META))
def test_wip_exact_availability_filters_match_pivot_and_export(app, availability_jobs, field, status):
    with app.app_context():
        args = MultiDict([(field, status)])
        result = get_wip_overview(args)
        expected_map = result["mat_status_map"] if field == "fabric_status" else result["comp_status_map"]
        expected = {
            job.job_num for job in WorksOrder.query.all()
            if expected_map.get(str(job.order_num), "no_data") == status
        }
        assert {job.job_num for job in result["jobs"].items} == expected
        assert result["jobs"].total == len(expected)
        assert sum(row["jobs"] for row in result["op_totals"].values()) == len(expected)
        assert sum(row["qty"] for row in result["op_totals"].values()) == len(expected) * 2
        exported = list(csv.DictReader(io.StringIO(get_wip_export(args).get_data().decode("utf-8-sig"))))
        assert {row["Job Number"] for row in exported} == expected
        if status == "no_data":
            column = "Order Fabric Status" if field == "fabric_status" else "Order Comp. Status"
            assert all(row[column] == "No Data" for row in exported)


def test_wip_combines_availability_with_scope_and_legacy_filters(app, availability_jobs):
    with app.app_context():
        db.session.add(Department(code="UPH", name="Upholstery", op_code="UPH"))
        db.session.add(Department(code="FRM", name="Frame", op_code="FRM"))
        db.session.commit()
        args = MultiDict([
            ("fabric_status", "ok"), ("component_status", "high_risk"),
            ("shortages_only", "1"), ("shortage_group", "component"),
            ("plan_week", "2610"), ("plan_sequence", "01"),
            ("dept", "Upholstery"), ("q", "STATUS"),
        ])
        result = get_wip_overview(args)
        assert [job.job_num for job in result["jobs"].items] == ["STATUS-1"]
        exported = list(csv.DictReader(io.StringIO(get_wip_export(args).get_data().decode("utf-8-sig"))))
        assert [row["Job Number"] for row in exported] == ["STATUS-1"]
        args["dept"] = "Frame"
        assert get_wip_overview(args)["jobs"].total == 0
        assert len(list(csv.DictReader(io.StringIO(get_wip_export(args).get_data().decode("utf-8-sig"))))) == 0


def test_wip_empty_availability_intersection_has_no_jobs_or_chart(app, availability_jobs):
    with app.app_context():
        args = MultiDict([("fabric_status", "ok"), ("component_status", "ok")])
        result = get_wip_overview(args)
        assert result["jobs"].total == 0
        assert result["wip_ops"] == []
        assert result["wip_chart_datasets"] == []
        assert len(list(csv.DictReader(io.StringIO(get_wip_export(args).get_data().decode("utf-8-sig"))))) == 0


@pytest.mark.parametrize("field", ["fabric_status", "component_status"])
def test_wip_rejects_invalid_availability_status(app, field):
    with app.app_context():
        args = MultiDict([(field, "invalid")])
        with pytest.raises(BadRequest):
            get_wip_overview(args)
        with pytest.raises(BadRequest):
            get_wip_export(args)


def test_wip_no_shortage_matches_legacy_behavior_and_empty_issues(app):
    with app.app_context():
        db.session.add(WorksOrder(
            job_num="CLEAR", order_num=10, model="MODEL", assembly_seq=0,
            job_released=True, job_complete=False, next_op="UPH", prod_plnwk="261001",
        ))
        db.session.commit()
        args = MultiDict([("shortages_only", "1"), ("shortage_group", "no_shortage")])
        assert get_wip_overview(args)["jobs"].total == 1
        args["shortage_group"] = "either"
        result = get_wip_overview(args)
        assert result["jobs"].total == 0
        assert result["wip_ops"] == []


def test_wip_availability_controls_and_links_preserve_selection(client, admin_user, availability_jobs):
    login(client, "admin@test.com", "Admin!Pass1234")
    response = client.get("/operations/wip?fabric_status=ok&component_status=high_risk")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="fabric_status"' in html
    assert 'id="component_status"' in html
    assert '<option value="ok" selected>Available (Mat. OK)</option>' in html
    assert '<option value="high_risk" selected>Shortage</option>' in html
    assert "Both selections must match" in html
    assert "No Data is not confirmed availability" in html
    class LinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.links.extend(value for key, value in attrs if key == "href")

    parser = LinkParser()
    parser.feed(html)
    for link in parser.links:
        url = urlparse(link)
        query = parse_qs(url.query)
        if url.path == "/operations/wip/export" or "page" in query:
            assert query["fabric_status"] == ["ok"]
            assert query["component_status"] == ["high_risk"]


def test_wip_availability_pagination_filters_before_paging(app, availability_jobs):
    with app.app_context():
        db.session.add_all([
            WorksOrder(
                job_num=f"READY-{index:02d}", order_num=1, model="MODEL",
                assembly_seq=0, job_released=True, job_complete=False,
                next_op="UPH", prod_plnwk="261001", required_qty=1,
            )
            for index in range(30)
        ])
        db.session.commit()
        args = MultiDict([
            ("fabric_status", "ok"), ("component_status", "high_risk"),
            ("per_page", "25"), ("page", "2"),
        ])
        result = get_wip_overview(args)
        assert result["jobs"].total == 31
        assert len(result["jobs"].items) == 6
        assert sum(row["jobs"] for row in result["op_totals"].values()) == 31
        exported = list(csv.DictReader(io.StringIO(get_wip_export(args).get_data().decode("utf-8-sig"))))
        assert len(exported) == 31


class FilterLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = {}
        self.current_link = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current_link = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data):
        if self.current_link is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current_link is not None:
            self.links[" ".join("".join(self.text).split())] = self.current_link
            self.current_link = None


def test_wip_compact_panel_chips_and_presets(client, admin_user, availability_jobs, db_session):
    db_session.add(Department(code="UPH", name="Upholstery", op_code="UPH"))
    db_session.commit()
    login(client, "admin@test.com", "Admin!Pass1234")
    args = {
        "category": "models", "q": "STATUS", "fabric_status": "ok",
        "component_status": "high_risk", "plan_week": "2610",
        "plan_sequence": "01", "dept": "Upholstery", "per_page": "25",
        "shortages_only": "1", "shortage_group": "component", "page": "2",
    }
    response = client.get("/operations/wip", query_string=args)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<details id="wip-filter-panel" class="mt-2">' in html
    assert 'aria-label="Applied filters"' in html
    assert "<legend" in html
    assert "Apply filters" in html
    assert 'data-wip-material-preset' in html
    assert 'id="wip-preset-feedback"' in html
    assert "8<span" in html
    assert 'name="dept" class="form-select' in html
    assert 'name="plan_week" class="form-select' in html
    assert 'name="plan_sequence" class="form-select' in html

    parser = FilterLinkParser()
    parser.feed(html)
    for label, link in parser.links.items():
        if label.startswith(("Show:", "Search:", "Materials:", "Fabric / Hide:", "Components:", "Week:", "Sequence:", "Department:")):
            query = parse_qs(urlparse(link).query, keep_blank_values=True)
            prefix = label.split(":")[0]
            removed = {
                "Show": "category", "Search": "q", "Materials": "shortage_group",
                "Fabric / Hide": "fabric_status", "Components": "component_status",
                "Week": "plan_week", "Sequence": "plan_sequence", "Department": "dept",
            }[prefix]
            assert query[removed] == [""]
            assert "page" not in query
            for key, value in args.items():
                if key == removed or key == "page":
                    continue
                if removed == "plan_week" and key == "plan_sequence":
                    assert query[key] == [""]
                elif removed == "shortage_group" and key == "shortages_only":
                    assert query[key] == ["0"]
                else:
                    assert query[key] == [value]

    clear_query = parse_qs(urlparse(parser.links["Clear all"]).query, keep_blank_values=True)
    assert clear_query == {"category": [""], "per_page": ["25"]}
    for preset, expected in [
        ("Both available", {"shortages_only": "0", "shortage_group": "", "fabric_status": "ok", "component_status": "ok"}),
        ("Any material issues", {"shortages_only": "1", "shortage_group": "either", "fabric_status": "", "component_status": ""}),
        ("No confirmed shortages", {"shortages_only": "1", "shortage_group": "no_shortage", "fabric_status": "", "component_status": ""}),
        ("Epicor shortage flag", {"shortages_only": "1", "shortage_group": "mtl_flag", "fabric_status": "", "component_status": ""}),
    ]:
        query = parse_qs(urlparse(parser.links[preset]).query, keep_blank_values=True)
        for key, value in expected.items():
            assert query[key] == [value]
        for key in ("category", "q", "plan_week", "plan_sequence", "dept", "per_page"):
            assert query[key] == [args[key]]
        assert "page" not in query


def test_wip_compact_form_uses_one_control_per_filter(client, admin_user, availability_jobs):
    login(client, "admin@test.com", "Admin!Pass1234")
    response = client.get("/operations/wip?category=parts&plan_week=unplanned")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    form = html.split('id="wip-filter-form"', 1)[1].split("</form>", 1)[0]
    for name in ("dept", "plan_week", "plan_sequence", "fabric_status", "component_status", "shortages_only", "shortage_group"):
        assert form.count(f'name="{name}"') == 1
    assert form.count('name="category"') == 3
    assert 'type="hidden" name="category"' not in form
    assert 'id="wip-category-3" value="parts" checked' in form
    assert 'id="wip-plan-sequence" name="plan_sequence" class="form-select form-select-sm" aria-describedby="wip-sequence-help" disabled' in form


def test_wip_sequence_choices_are_grouped_by_week(app):
    with app.app_context():
        common = dict(
            assembly_seq=0, job_released=True, job_complete=False,
            model="MODEL", next_op="UPH", required_qty=1,
        )
        db.session.add_all([
            WorksOrder(job_num="W10B", prod_plnwk="261002", **common),
            WorksOrder(job_num="W10A", prod_plnwk="261001", **common),
            WorksOrder(job_num="W11C", prod_plnwk="261103", **common),
            WorksOrder(job_num="W12", prod_plnwk="2612", **common),
        ])
        db.session.commit()
        result = get_wip_overview(MultiDict([("plan_week", "2610")]))
        assert result["plan_week_options"] == ["2610", "2611", "2612"]
        assert result["plan_sequences_by_week"] == {"2610": ["01", "02"], "2611": ["03"]}
        assert result["plan_sequence_options"] == ["01", "02"]
