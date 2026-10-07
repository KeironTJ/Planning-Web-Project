"""Work views share filtering, pagination, access and return navigation."""

from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_projects import BASE, create, grant, patch, sign_in


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


class ListParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = {}
        self.row = None
        self.column = -1
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-work-row" in attrs:
            self.row = {"attrs": attrs, "cells": [], "links": []}
            self.rows[attrs["data-work-row"]] = self.row
        elif self.row is not None and tag == "td":
            self.row["cells"].append("")
            self.column = len(self.row["cells"]) - 1
        elif self.row is not None and tag == "a":
            self.row["links"].append(attrs["href"])

    def handle_data(self, data):
        if self.row is not None and self.column >= 0:
            self.row["cells"][self.column] += data

    def handle_endtag(self, tag):
        if tag == "tr":
            self.row = None
            self.column = -1


def row_key(item):
    return f"{item['kind']}:{item['id']}"


@pytest.fixture
def breakdown(signed_client):
    project = create(signed_client, "projects", name="Packaging improvement")
    activity = create(
        signed_client, "activities", name="Run trial", project_id=project["id"]
    )
    task = create(signed_client, name="Inspect samples", activity_id=activity["id"])
    subtask = create(signed_client, name="Record results", parent_task_id=task["id"])
    direct = create(signed_client, name="Order equipment", project_id=project["id"])
    return project, activity, task, subtask, direct


@pytest.mark.parametrize("kind", ["projects", "activities"])
def test_list_breaks_down_descendants_once(signed_client, breakdown, kind):
    project, activity, task, subtask, direct = breakdown
    html = signed_client.get(f"/projects/{kind}?view=list").get_data(as_text=True)
    rows = ListParser(html).rows
    expected = breakdown if kind == "projects" else (activity, task, subtask)
    assert set(rows) == {row_key(item) for item in expected}
    assert list(rows).index(row_key(task)) < list(rows).index(row_key(subtask))
    context = rows[row_key(subtask)]["cells"][1]
    for ancestor in (project, activity, task):
        assert ancestor["name"] in context
        assert ancestor["reference"] in context
    assert "Subtask" in rows[row_key(subtask)]["cells"][0]
    assert "Standalone" not in context
    assert rows[row_key(subtask)]["attrs"]["data-work-depth"] == "3"
    assert "data-work-key" not in html


def test_task_list_context_includes_parents_outside_filtered_page(
    signed_client, breakdown
):
    project, activity, task, subtask, direct = breakdown
    response = signed_client.get(
        "/projects/tasks",
        query_string={"view": "list", "q": subtask["name"], "per_page": 1},
    )
    rows = ListParser(response.get_data(as_text=True)).rows
    assert set(rows) == {row_key(subtask)}
    context = rows[row_key(subtask)]["cells"][1]
    for ancestor in (project, activity, task):
        assert ancestor["name"] in context
        assert any(
            urlsplit(link).path == f"/projects/{ancestor['kind']}/{ancestor['id']}"
            for link in rows[row_key(subtask)]["links"]
        )
    assert "Standalone" not in context
    assert "Page 1 of 1 / 1 items" in response.get_data(as_text=True)


def test_standalone_subtask_list_names_its_parent(signed_client):
    parent = create(signed_client, name="Independent parent")
    subtask = create(signed_client, name="Independent child", parent_task_id=parent["id"])
    html = signed_client.get("/projects/tasks?view=list").get_data(as_text=True)
    rows = ListParser(html).rows
    assert "Standalone task" in rows[row_key(parent)]["cells"][1]
    assert parent["name"] in rows[row_key(subtask)]["cells"][1]
    assert "Standalone" not in rows[row_key(subtask)]["cells"][1]


@pytest.mark.parametrize("kind", ["projects", "activities"])
def test_detail_exposes_work_list_outside_collapsed_progress(
    signed_client, breakdown, kind
):
    project, activity, task, subtask, direct = breakdown
    item = project if kind == "projects" else activity
    path = f"/projects/{kind}/{item['id']}"
    default_html = signed_client.get(path).get_data(as_text=True)
    progress_end = default_html.index(
        "</details>", default_html.index('data-state-key="progress"')
    )
    assert default_html.index("Work breakdown") > progress_end
    assert "Hierarchy view" in ViewParser(default_html).sections
    response = signed_client.get(
        path, query_string={
            "view": "list", "sort": "deadline",
            "return_to": "/projects/projects?view=list",
        }
    )
    html = response.get_data(as_text=True)
    parsed = ViewParser(html)
    assert set(parsed.views) == {"list", "hierarchy"}
    assert parsed.views["list"]["aria-current"] == "page"
    assert "List view" in parsed.sections
    assert "Hierarchy view" not in parsed.sections
    rows = ListParser(html).rows
    expected = breakdown if kind == "projects" else (activity, task, subtask)
    assert set(rows) == {row_key(row) for row in expected}
    assert project["name"] in rows[row_key(subtask)]["cells"][1]
    for view, link in parsed.views.items():
        assert parse_qs(urlsplit(link["href"]).query) == {
            "view": [view], "sort": ["deadline"],
            "return_to": ["/projects/projects?view=list"],
        }
    assert signed_client.get(path + "?view=board").status_code == 400


def test_project_list_paginates_projects_not_descendants(signed_client, breakdown):
    project, activity, task, subtask, direct = breakdown
    second = create(signed_client, "projects", name="Second project")
    second_task = create(signed_client, name="Second task", project_id=second["id"])
    html = signed_client.get("/projects/projects?view=list&per_page=1").get_data(as_text=True)
    assert set(ListParser(html).rows) == {row_key(item) for item in breakdown}
    assert "Page 1 of 2 / 2 items" in html
    html = signed_client.get("/projects/projects?view=list&per_page=1&page=2").get_data(as_text=True)
    assert set(ListParser(html).rows) == {row_key(second), row_key(second_task)}


def test_reports_list_uses_subtask_parent_context(signed_client, breakdown):
    project, activity, task, subtask, direct = breakdown
    assert patch(signed_client, subtask, deadline="2000-01-01").status_code == 200
    html = signed_client.get("/projects/reports").get_data(as_text=True)
    context = ListParser(html).rows[row_key(subtask)]["cells"][1]
    assert all(item["name"] in context for item in (project, activity, task))


def test_project_list_filters_select_parents_not_child_status(signed_client, breakdown):
    project, activity, task, subtask, direct = breakdown
    assert patch(signed_client, subtask, status="active").status_code == 200
    html = signed_client.get(
        "/projects/projects?view=list&status=planned"
    ).get_data(as_text=True)
    rows = ListParser(html).rows
    assert set(rows) == {row_key(item) for item in breakdown}
    assert "Active" in rows[row_key(subtask)]["cells"][2]


def test_empty_project_list_keeps_project_row(signed_client):
    project = create(signed_client, "projects", name="No child work")
    html = signed_client.get("/projects/projects?view=list").get_data(as_text=True)
    assert set(ListParser(html).rows) == {row_key(project)}


@pytest.mark.parametrize("parent_kind", ["projects", "activities", None])
def test_subtask_context_for_each_parent_location(signed_client, parent_kind):
    context = {}
    location = None
    if parent_kind:
        location = create(signed_client, parent_kind, name="Named location")
        context["project_id" if parent_kind == "projects" else "activity_id"] = location["id"]
    parent = create(signed_client, name="Named parent task", **context)
    subtask = create(signed_client, name="Named subtask", parent_task_id=parent["id"])
    html = signed_client.get(
        "/projects/tasks?view=list&q=Named+subtask"
    ).get_data(as_text=True)
    rows = ListParser(html).rows
    assert set(rows) == {row_key(subtask)}
    context_cell = rows[row_key(subtask)]["cells"][1]
    assert parent["name"] in context_cell
    assert parent["reference"] in context_cell
    assert "Standalone" not in context_cell
    if location:
        assert location["name"] in context_cell
        assert location["reference"] in context_cell


def test_shared_project_list_excludes_private_archived_work_and_edit_controls(
    signed_client, client, viewer_user, breakdown
):
    project, activity, task, subtask, direct = breakdown
    archived = create(signed_client, name="Archived child", parent_task_id=task["id"])
    assert signed_client.delete(
        f"{BASE}/tasks/{archived['id']}", json={"version": archived["version"]}
    ).status_code == 200
    private = create(signed_client, "projects", name="Private project")
    create(signed_client, name="Private child", project_id=private["id"])
    grant(signed_client, project, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    for path in (
        "/projects/projects?view=list",
        f"/projects/projects/{project['id']}?view=list",
    ):
        html = client.get(path).get_data(as_text=True)
        rows = ListParser(html).rows
        assert set(rows) == {row_key(item) for item in breakdown}
        assert "Archived child" not in html
        assert "Private project" not in html
        assert "Private child" not in html
        assert "data-quick-edit " not in html
        if "/projects/projects/" in path:
            assert "data-quick-add " not in html
