"""Work views share filtering, pagination, access and return navigation."""

from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_projects import create, sign_in


class ViewParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.views = {}
        self.sections = []
        self.inputs = {}
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs)
            if "data-work-view" in attrs:
                self.views[attrs["data-work-view"]] = attrs
        elif tag == "section":
            self.sections.append(attrs.get("aria-label"))
        elif tag == "input":
            self.inputs[attrs.get("name")] = attrs.get("value")


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_hierarchy_default_and_available_views(signed_client, kind):
    create(signed_client, kind)
    response = signed_client.get(f"/projects/{kind}")
    assert response.status_code == 200
    parsed = ViewParser(response.get_data(as_text=True))
    assert set(parsed.views) == (
        {"list", "hierarchy", "board", "timeline"} if kind == "tasks"
        else {"list", "hierarchy", "timeline"}
    )
    assert parsed.views["hierarchy"]["aria-current"] == "page"
    assert "Hierarchy view" in parsed.sections
    assert "List view" not in parsed.sections
    assert "Board view" not in parsed.sections
    assert parsed.inputs["view"] == "hierarchy"


@pytest.mark.parametrize(
    ("kind", "view"),
    [
        ("projects", "list"),
        ("activities", "list"),
        ("tasks", "list"),
        ("tasks", "board"),
        ("tasks", "hierarchy"),
        ("projects", "timeline"),
        ("activities", "timeline"),
        ("tasks", "timeline"),
    ],
)
def test_one_view_preserves_filters_pagination_and_return(signed_client, kind, view):
    for number in range(3):
        create(signed_client, kind, name=f"View item {number}")
    response = signed_client.get(
        f"/projects/{kind}",
        query_string={
            "view": view,
            "scope": "mine",
            "status": "planned",
            "per_page": 1,
            "page": 2,
        },
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    parsed = ViewParser(html)
    assert [label for label in parsed.sections if label in {
        "List view", "Hierarchy view", "Board view", "Timeline view"
    }] == [f"{view.title()} view"]
    assert parsed.views[view]["aria-current"] == "page"
    assert sum("aria-current" in link for link in parsed.views.values()) == 1
    assert parsed.inputs["view"] == view
    assert parsed.inputs["per_page"] == "1"
    assert "page" not in parsed.inputs
    assert "Page 2 of 3 / 3 items" in html
    for selected, link in parsed.views.items():
        assert parse_qs(urlsplit(link["href"]).query) == {
            "view": [selected],
            "scope": ["mine"],
            "status": ["planned"],
            "per_page": ["1"],
            "page": ["2"],
        }
    reset = next(link for link in parsed.links if "data-work-reset" in link)
    assert parse_qs(urlsplit(reset["href"]).query) == {"view": [view]}
    assert any(
        parse_qs(urlsplit(link["href"]).query).get("page") == ["3"]
        and parse_qs(urlsplit(link["href"]).query).get("view") == [view]
        for link in parsed.links
    )
    detail_links = [
        urlsplit(unescape(link["href"]))
        for link in parsed.links
        if urlsplit(link["href"]).path.startswith(f"/projects/{kind}/")
        and not urlsplit(link["href"]).path.endswith("/new")
    ]
    assert detail_links
    assert all(
        parse_qs(urlsplit(parse_qs(link.query)["return_to"][0]).query)["view"] == [view]
        for link in detail_links
    )
    if view == "board":
        assert "Columns show only the tasks on this filtered page" in html
        assert "data-scroll-key=\"task-board\"" in html
    if view != "hierarchy":
        assert "data-work-key" not in html


@pytest.mark.parametrize(
    ("kind", "view"),
    [("projects", "board"), ("activities", "board"), ("tasks", ""), ("tasks", "unknown")],
)
def test_invalid_views_are_explicit_errors(signed_client, kind, view):
    response = signed_client.get(f"/projects/{kind}", query_string={"view": view})
    assert response.status_code == 400
    assert b"Choose a valid work view" in response.data


@pytest.mark.parametrize("view", ["list", "hierarchy", "board", "timeline"])
def test_all_views_exclude_private_work_and_handle_empty_results(
    signed_client, client, viewer_user, view
):
    create(signed_client, name="Private view task")
    sign_in(client, viewer_user)
    response = client.get("/projects/tasks", query_string={"view": view})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Private view task" not in html
    assert "Page 1 of 1 / 0 items" in html
    assert (
        "No tasks in this status." if view == "board" else "No matching work items."
    ) in html
