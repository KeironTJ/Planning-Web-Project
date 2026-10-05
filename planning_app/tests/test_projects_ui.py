"""Workflow presentation and visibility-safe, readable log navigation."""

import pytest

from tests.test_projects import BASE, create, patch, sign_in


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


def test_workflow_statuses_and_navigation(signed_client):
    task = create(signed_client)
    for path in ("/projects/", "/projects/tasks", f"/projects/tasks/{task['id']}"):
        html = signed_client.get(path).get_data(as_text=True)
        assert "css/projects.css" in html
        assert "work-status-planned" in html
        nav = html.split('aria-label="Project tracking">')[1].split("</nav>")[0]
        assert "Standalone tasks" not in nav
        assert "Logs &amp; updates" in nav
        assert 'aria-current="page"' in nav
        assert "#logs" in html
    for kind in ("projects", "activities", "tasks"):
        html = signed_client.get(f"/projects/{kind}").get_data(as_text=True)
        assert f"<h1>{kind.title()}</h1>" in html
        assert 'class="work-filter-panel card mb-4"' in html
        assert "Quick filters" in html
    assert "<h1>My dashboard</h1>" in signed_client.get("/projects/").get_data(
        as_text=True
    )
    active = patch(signed_client, task, status="active").get_json()
    assert (
        b"work-status-active" in signed_client.get(f"/projects/tasks/{task['id']}").data
    )
    patch(signed_client, active, status="blocked")
    assert b"work-status-blocked" in signed_client.get("/projects/tasks").data
    assert b"Workflow status guide" in signed_client.get("/projects/").data


def test_detail_summary_and_delete_guidance(signed_client):
    project = create(signed_client, "projects", name="Detail summary project")
    create(signed_client, "activities", project_id=project["id"])
    html = signed_client.get(f"/projects/projects/{project['id']}").data
    assert b"work-detail-summary" in html
    assert b"work-reference" in html
    assert b"Delete item" in html
    assert b"Move or delete its 1 child item(s) first." in html
    assert b"Confirm archive" not in html


def test_readable_logs_filters_and_pagination(signed_client, planner_user):
    project = create(signed_client, "projects", name="Readable project")
    task = create(signed_client, name="Readable task", project_id=project["id"])
    patch(signed_client, task, status="active")
    signed_client.post(
        f"{BASE}/logs",
        json={
            "task_id": task["id"],
            "body": "Shift handover: trial started.",
        },
    )
    html = signed_client.get("/projects/logs").get_data(as_text=True)
    assert "Shift handover: trial started." in html
    assert "Status: Planned -&gt; Active" in html
    assert "Task: Readable task" in html and "Project: Readable project" in html
    assert "Automatic update" in html
    comments = signed_client.get(f"{BASE}/logs?log_kind=comment").get_json()
    assert comments["total"] == 1
    assert comments["items"][0]["target_name"] == "Readable task"
    assert comments["items"][0]["summary"] == "Comment added"
    project_logs = signed_client.get(
        f"{BASE}/logs?target_kind=projects&target_id={project['id']}"
    ).get_json()
    assert project_logs["total"] == 1
    assert (
        signed_client.get(f"{BASE}/logs?author_id={planner_user.id}").get_json()[
            "total"
        ]
        == 5
    )
    assert signed_client.get("/projects/logs?per_page=2").status_code == 200
    for filters in (
        "log_kind=unknown",
        "target_kind=unknown",
        "target_id=1",
        "author_id=bad",
    ):
        assert signed_client.get(f"/projects/logs?{filters}").status_code == 400
    assert b"Recent logs &amp; updates" in signed_client.get("/projects/").data


def test_logs_search_by_work_name_or_reference(signed_client):
    project = create(signed_client, "projects", name="Searchable project")
    task = create(signed_client, name="Searchable task", project_id=project["id"])
    signed_client.post(
        f"{BASE}/logs",
        json={"task_id": task["id"], "body": "Searchable handover"},
    )

    by_name = signed_client.get(f"{BASE}/logs?target_search=Searchable%20task").get_json()
    by_reference = signed_client.get(
        f"{BASE}/logs?target_search={task['reference']}"
    ).get_json()
    assert by_name["total"] == by_reference["total"]
    assert by_name["total"] > 0
    assert all(entry["target_id"] == task["id"] for entry in by_reference["items"])
    assert (
        signed_client.get("/projects/logs?target_search=Searchable%20task").status_code
        == 200
    )
    assert signed_client.get(f"{BASE}/logs?target_search={'x' * 201}").status_code == 400


def test_logs_never_show_private_work(signed_client, client, viewer_user):
    project = create(signed_client, "projects", name="Confidential workflow")
    signed_client.post(
        f"{BASE}/logs",
        json={
            "project_id": project["id"],
            "body": "Private comment",
        },
    )
    sign_in(client, viewer_user)
    response = client.get("/projects/logs")
    assert response.status_code == 200
    assert b"Confidential workflow" not in response.data
    assert b"Private comment" not in response.data
    assert b"No updates yet" in response.data
    assert client.get(f"{BASE}/logs?log_kind=comment").get_json()["total"] == 0
    assert (
        client.get(
            f"{BASE}/logs?target_search=Confidential%20workflow"
        ).get_json()["total"]
        == 0
    )
