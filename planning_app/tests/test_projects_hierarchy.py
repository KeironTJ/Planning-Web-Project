"""Expandable work views preserve hierarchy, filters and access boundaries."""

from html.parser import HTMLParser
from html import escape
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from app.projects import services as svc
from app.projects.models import Project, Task
from tests.test_projects import BASE, create, grant, patch, sign_in


class WorkTreeParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.paths = {}
        self.context = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            attrs = dict(attrs)
            key = attrs.get("data-work-key")
            self.stack.append(key)
            if key:
                assert key not in self.paths, f"Duplicate work node: {key}"
                self.paths[key] = tuple(value for value in self.stack if value)
                self.context[key] = attrs["data-context-only"] == "true"

    def handle_endtag(self, tag):
        if tag == "li":
            self.stack.pop()


def key(item):
    return f"{item['kind']}:{item['id']}"


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


@pytest.fixture
def work(signed_client):
    project = create(signed_client, "projects", name="Line improvement", budget="100")
    empty_project = create(signed_client, "projects", name="Empty project")
    activity = create(
        signed_client,
        "activities",
        name="Process trial",
        project_id=project["id"],
        budget="20",
    )
    empty_activity = create(
        signed_client, "activities", name="Empty activity", project_id=project["id"]
    )
    standalone_activity = create(signed_client, "activities", name="Independent trial")
    direct = create(
        signed_client, name="Direct project task", project_id=project["id"], budget="10"
    )
    nested = create(
        signed_client, name="Nested trial task", activity_id=activity["id"], budget="5"
    )
    standalone_nested = create(
        signed_client,
        name="Independent trial task",
        activity_id=standalone_activity["id"],
    )
    standalone_task = create(signed_client, name="Standalone check")
    return {
        "project": project,
        "empty_project": empty_project,
        "activity": activity,
        "empty_activity": empty_activity,
        "standalone_activity": standalone_activity,
        "direct": direct,
        "nested": nested,
        "standalone_nested": standalone_nested,
        "standalone_task": standalone_task,
    }


def tree(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.get_data(as_text=True)
    return WorkTreeParser(response.get_data(as_text=True))


def test_dashboard_places_every_variant_once(signed_client, work):
    parsed = tree(signed_client, "/projects/")
    assert len(parsed.paths) == len(work)
    assert parsed.paths[key(work["nested"])] == (
        key(work["project"]),
        key(work["activity"]),
        key(work["nested"]),
    )
    assert parsed.paths[key(work["direct"])] == (
        key(work["project"]),
        key(work["direct"]),
    )
    assert parsed.paths[key(work["standalone_nested"])] == (
        key(work["standalone_activity"]),
        key(work["standalone_nested"]),
    )
    assert parsed.paths[key(work["standalone_task"])] == (key(work["standalone_task"]),)
    assert not any(parsed.context.values())


@pytest.mark.parametrize(
    "status, primary, secondary",
    [
        ("planned", "Start", ["Cancel / close"]),
        ("active", "Complete", ["Mark blocked", "Cancel / close"]),
        ("blocked", "Start / resume", ["Cancel / close"]),
        ("completed", "Reopen", []),
        ("cancelled", "Restart", []),
    ],
)
def test_hierarchy_action_bar_prioritizes_workflow(signed_client, status, primary, secondary):
    item = create(signed_client, name="Action placement task")
    if status in ("blocked", "completed"):
        item = patch(signed_client, item, status="active").get_json()
    if status != "planned":
        item = patch(signed_client, item, status=status).get_json()
    origin = "/projects/tasks?view=hierarchy"
    html = signed_client.get(origin).get_data(as_text=True)
    bar = html.split('class="work-node-actions"')[1].split("</li>")[0]
    main, _, more = bar.partition('<details class="work-node-more">')
    assert f">{primary}</button>" in main
    assert "data-quick-edit " in main
    assert "Logs &amp; comments" in main
    assert ("More workflow actions" in more) == bool(secondary)
    for label in secondary:
        assert f">{label}</button>" not in main
        assert f">{label}</button>" in more
    assert bar.count(f'action="/projects/tasks/{item["id"]}/status"') == 1 + len(secondary)
    assert bar.count('name="csrf_token"') == 1 + len(secondary)
    assert bar.count(f'name="version" value="{item["version"]}"') == 1 + len(secondary)
    assert bar.count(f'name="return_to" value="{escape(origin, quote=True)}"') == 1 + len(secondary)


def test_hierarchy_child_buttons_belong_to_children(signed_client, work):
    html = signed_client.get("/projects/").get_data(as_text=True)
    toolbars = re.findall(
        r'<div class="work-child-toolbar">(.*?)</div>\s*</div>',
        html, re.DOTALL,
    )
    assert toolbars
    for toolbar in toolbars:
        assert "data-quick-edit" not in toolbar
        for href in re.findall(r'href="([^"]+)"', toolbar):
            url = urlsplit(href.replace("&amp;", "&"))
            query = parse_qs(url.query)
            assert query["return_to"] == ["/projects/"]
            if url.path == "/projects/activities/new":
                assert "project_id" in query and "activity_id" not in query
            else:
                assert url.path == "/projects/tasks/new"
                assert ("project_id" in query) != ("activity_id" in query)
    assert all(
        "data-quick-add" not in bar
        for bar in re.findall(
            r'<div class="work-node-actions".*?</div>', html, re.DOTALL
        )
    )


def test_hierarchy_open_actions_are_prominent_and_names_link_to_details(signed_client, work):
    origin = "/projects/"
    html = signed_client.get(origin).get_data(as_text=True)
    bars = re.findall(
        r'<div class="work-node-actions"[^>]*>\s*(<a[^>]*>.*?</a>)',
        html, re.DOTALL,
    )
    assert len(bars) == len(work)
    for item in work.values():
        path = f"/projects/{item['kind']}/{item['id']}"
        label = f"Open {svc.KIND_LABELS[item['kind']]}: {item['name']}"
        opening = next(link for link in bars if f'aria-label="{label}"' in link)
        assert 'class="btn btn-sm btn-primary"' in opening
        assert "bi-arrow-right" in opening
        url = urlsplit(re.search(r'href="([^"]+)"', opening).group(1))
        assert url.path == path
        assert parse_qs(url.query)["return_to"] == [origin]
        if item["kind"] != "tasks":
            assert re.search(
                rf'<a class="fw-semibold text-break"[^>]*aria-label="{re.escape(label)}">',
                html,
            )


def test_hierarchy_viewer_bars_offer_navigation_only(signed_client, client, viewer_user):
    task = create(signed_client, name="Viewer actions")
    grant(signed_client, task, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    html = client.get("/projects/tasks?view=hierarchy").get_data(as_text=True)
    assert 'aria-label="Navigation for Viewer actions"' in html
    assert 'aria-label="Open task: Viewer actions"' in html
    assert "Logs &amp; comments" in html
    assert "work-node-more" not in html
    assert 'action="/projects/tasks/' not in html
    assert "data-quick-edit " not in html


def test_project_list_and_details_share_nested_structure(signed_client, work):
    parsed = tree(signed_client, "/projects/projects")
    assert len(parsed.paths) == 6
    assert key(work["standalone_activity"]) not in parsed.paths
    assert parsed.paths[key(work["nested"])] == (
        key(work["project"]),
        key(work["activity"]),
        key(work["nested"]),
    )
    parsed = tree(signed_client, f"/projects/projects/{work['project']['id']}")
    assert len(parsed.paths) == 5
    assert parsed.paths[key(work["direct"])] == (
        key(work["project"]),
        key(work["direct"]),
    )
    response = signed_client.get(f"/projects/projects/{work['project']['id']}")
    assert (
        f'data-state-key="{key(work["project"])}" open>'.encode() in response.data
    )
    assert b"No tasks." in response.data
    assert b"2 activities shown" in response.data
    assert b"2 tasks shown" in response.data
    assert b">Activity</span>" in response.data
    assert b">Activitie</span>" not in response.data
    empty = signed_client.get(f"/projects/projects/{work['empty_project']['id']}")
    assert b"No activities or direct tasks." in empty.data


def test_activity_list_preserves_project_and_standalone_context(signed_client, work):
    parsed = tree(signed_client, "/projects/activities")
    assert parsed.context[key(work["project"])]
    assert not parsed.context[key(work["activity"])]
    assert key(work["direct"]) not in parsed.paths
    assert parsed.paths[key(work["nested"])] == (
        key(work["project"]),
        key(work["activity"]),
        key(work["nested"]),
    )
    assert parsed.paths[key(work["standalone_nested"])] == (
        key(work["standalone_activity"]),
        key(work["standalone_nested"]),
    )
    detail = tree(signed_client, f"/projects/activities/{work['activity']['id']}")
    assert detail.paths[key(work["nested"])] == (
        key(work["activity"]),
        key(work["nested"]),
    )
    assert key(work["project"]) not in detail.paths


def test_task_filters_keep_ancestors_without_siblings(signed_client, work):
    completed = patch(signed_client, work["nested"], status="active").get_json()
    patch(signed_client, completed, status="completed")
    parsed = tree(signed_client, "/projects/tasks?status=completed")
    assert set(parsed.paths) == {
        key(work["project"]),
        key(work["activity"]),
        key(work["nested"]),
    }
    assert parsed.context[key(work["project"])]
    assert parsed.context[key(work["activity"])]
    assert not parsed.context[key(work["nested"])]
    response = signed_client.get("/projects/tasks?status=completed")
    assert b"1 activities shown" in response.data
    assert b"1 tasks shown" in response.data
    assert b"100.0% complete" in response.data
    assert b"Shown work budget 5.00" in response.data
    parsed = tree(signed_client, "/projects/tasks?scope=standalone")
    assert set(parsed.paths) == {key(work["standalone_task"])}
    parsed = tree(signed_client, "/projects/tasks?scope=activity")
    assert key(work["direct"]) not in parsed.paths
    assert key(work["standalone_nested"]) in parsed.paths


def test_project_filters_include_children_and_paginate_only_projects(
    signed_client, work
):
    active = patch(signed_client, work["nested"], status="active").get_json()
    patch(signed_client, active, status="completed")
    parsed = tree(signed_client, "/projects/projects?status=planned&per_page=1&page=2")
    assert set(parsed.paths) == {
        key(work[name])
        for name in ("project", "activity", "empty_activity", "direct", "nested")
    }
    assert key(work["empty_project"]) not in parsed.paths
    response = signed_client.get("/projects/projects?status=planned&per_page=1&page=2")
    assert b"Page 2 of 2 / 2 items" in response.data
    assert b"Completed" in response.data


def test_breadcrumbs_show_full_parent_names(signed_client, work):
    response = signed_client.get(f"/projects/tasks/{work['nested']['id']}")
    html = response.get_data(as_text=True)
    breadcrumb = html.split('<nav aria-label="Work location">')[1].split("</nav>")[0]
    assert breadcrumb.index("Line improvement") < breadcrumb.index("Process trial")
    assert breadcrumb.index("Process trial") < breadcrumb.index("Nested trial task")
    assert f'href="/projects/projects/{work["project"]["id"]}"' in breadcrumb
    assert f'href="/projects/activities/{work["activity"]["id"]}"' in breadcrumb
    response = signed_client.get(f"/projects/tasks/{work['direct']['id']}")
    breadcrumb = (
        response.get_data(as_text=True)
        .split('<nav aria-label="Work location">')[1]
        .split("</nav>")[0]
    )
    assert "Line improvement" in breadcrumb and "Process trial" not in breadcrumb
    response = signed_client.get(f"/projects/tasks/{work['standalone_task']['id']}")
    assert b"Standalone task" in response.data


def test_hierarchy_aggregate_values(signed_client, planner_user, work):
    active = patch(signed_client, work["nested"], status="active").get_json()
    patch(signed_client, active, status="completed")
    project = Project.query.get(work["project"]["id"])
    nodes = svc.work_hierarchy([project], planner_user, include_ancestors=False)
    assert len(nodes) == 1
    node = nodes[0]
    assert node["activity_count"] == 2
    assert node["task_count"] == 2
    assert node["progress"] == {"completed": 1, "total": 2, "percent": 50.0}
    assert node["rollup"]["budget"] == "135.00"
    assert node["rollup"]["actuals"] == "0.00"
    assert [child["item"]["kind"] for child in node["children"]] == [
        "activities",
        "activities",
        "tasks",
    ]
    nested = Task.query.get(work["nested"]["id"])
    contextual = svc.work_hierarchy([nested], planner_user, include_children=False)[0]
    assert contextual["context_only"]
    assert contextual["rollup"]["budget"] == "5.00"


def test_tree_never_exposes_deleted_or_unshared_work(
    signed_client,
    client,
    planner_user,
    viewer_user,
    work,
):
    project = work["project"]
    share = grant(signed_client, project, user_id=viewer_user.id, role="viewer")[0]
    task = work["nested"]
    assert (
        signed_client.delete(
            f"{BASE}/tasks/{task['id']}",
            json={
                "version": task["version"],
            },
        ).status_code
        == 200
    )
    sign_in(client, viewer_user)
    parsed = tree(client, "/projects/")
    assert key(work["nested"]) not in parsed.paths
    assert key(work["project"]) in parsed.paths
    assert key(work["standalone_activity"]) not in parsed.paths
    assert key(work["standalone_task"]) not in parsed.paths
    sign_in(client, planner_user)
    client.delete(f"{BASE}/projects/{project['id']}/shares/{share['id']}")
    sign_in(client, viewer_user)
    assert tree(client, "/projects/").paths == {}
    project_model = Project.query.get(project["id"])
    assert svc.work_hierarchy([project_model], viewer_user) == []
