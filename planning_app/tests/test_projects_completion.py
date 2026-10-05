"""Discoverable status actions and independent count-based completion metrics."""

import pytest

from app.projects.models import LogEntry, Project
from tests.test_projects import BASE, create, grant, patch, sign_in


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


def status(client, item, value):
    return client.post(
        f"/projects/{item['kind']}/{item['id']}/status",
        data={
            "version": item["version"],
            "status": value,
        },
    )


def latest(client, item):
    return client.get(f"{BASE}/{item['kind']}/{item['id']}").get_json()


def finish(client, item):
    assert status(client, item, "active").status_code == 302
    active = latest(client, item)
    assert status(client, active, "completed").status_code == 302
    return latest(client, item)


def test_controls_start_complete_reopen_and_cancel(signed_client):
    task = create(signed_client)
    path = f"/projects/tasks/{task['id']}"
    assert b">Start</button>" in signed_client.get(path).data
    assert b">Cancel / close</button>" in signed_client.get(path).data
    assert status(signed_client, task, "active").status_code == 302
    active = latest(signed_client, task)
    assert b">Complete</button>" in signed_client.get(path).data
    assert status(signed_client, active, "completed").status_code == 302
    completed = latest(signed_client, task)
    assert b">Reopen</button>" in signed_client.get(path).data
    assert status(signed_client, completed, "active").status_code == 302
    reopened = latest(signed_client, task)
    assert status(signed_client, reopened, "cancelled").status_code == 302
    assert b">Restart</button>" in signed_client.get(path).data
    assert (
        status(signed_client, latest(signed_client, task), "planned").status_code == 302
    )
    actions = LogEntry.query.filter_by(task_id=task["id"], kind="audit").all()
    assert len(actions) == 6


def test_parent_close_requires_every_descendant_and_activity(signed_client):
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    nested = create(signed_client, activity_id=activity["id"])
    direct = create(signed_client, project_id=project["id"])
    assert status(signed_client, project, "active").status_code == 302
    project = latest(signed_client, project)
    assert status(signed_client, activity, "active").status_code == 302
    activity = latest(signed_client, activity)
    before_logs = LogEntry.query.count()
    response = status(signed_client, project, "completed")
    assert response.status_code == 302
    response = signed_client.get(response.location)
    assert b"1 activities and 2 tasks remain outstanding" in response.data
    assert LogEntry.query.count() == before_logs
    assert latest(signed_client, project)["status"] == "active"
    assert patch(signed_client, activity, status="completed").status_code == 409
    assert patch(signed_client, project, status="cancelled").status_code == 409
    finish(signed_client, nested)
    assert status(signed_client, direct, "cancelled").status_code == 302
    response = status(signed_client, project, "completed")
    assert response.status_code == 302
    assert b"1 activities and 0 tasks remain outstanding" in signed_client.get(
        response.location
    ).data
    assert status(signed_client, activity, "completed").status_code == 302
    assert status(signed_client, project, "completed").status_code == 302
    assert latest(signed_client, project)["status"] == "completed"
    detail = signed_client.get(f"/projects/projects/{project['id']}").data
    assert b"remain outstanding" not in detail


def test_reopen_and_new_work_require_open_ancestors(signed_client):
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    task = create(signed_client, activity_id=activity["id"])
    task = finish(signed_client, task)
    activity = finish(signed_client, activity)
    project = finish(signed_client, project)
    assert status(signed_client, task, "active").status_code == 409
    assert status(signed_client, activity, "active").status_code == 409
    assert (
        signed_client.post(
            f"{BASE}/tasks",
            json={
                "name": "Cannot add",
                "department": "Planning",
                "project_id": project["id"],
            },
        ).status_code
        == 409
    )
    assert status(signed_client, project, "active").status_code == 302
    assert status(signed_client, task, "active").status_code == 409
    assert status(signed_client, activity, "active").status_code == 302
    assert status(signed_client, task, "active").status_code == 302


def test_count_completion_is_independent_of_costs(signed_client):
    project = create(signed_client, "projects", budget="1000", actuals="1000")
    activity = create(signed_client, "activities", project_id=project["id"])
    first = create(
        signed_client, activity_id=activity["id"], budget="100", actuals="100"
    )
    second = create(signed_client, project_id=project["id"], budget="900")
    cancelled = create(signed_client, project_id=project["id"])
    finish(signed_client, first)
    status(signed_client, cancelled, "cancelled")
    report = signed_client.get(f"{BASE}/reports").get_json()
    assert report["progress"]["percent"] == 50.0
    assert report["completion"]["tasks"] == {
        "completed": 1,
        "total": 2,
        "outstanding": 1,
        "cancelled": 1,
        "percent": 50.0,
    }
    assert report["completion"]["activities"]["outstanding"] == 1
    assert report["completion"]["projects"]["outstanding"] == 1
    assert report["projects"][0]["completion"]["tasks"]["outstanding"] == 1
    summary = signed_client.get(f"{BASE}/projects/{project['id']}/summary").get_json()
    assert summary["completion"]["activities"]["outstanding"] == 1
    for path in (
        "/projects/",
        "/projects/reports",
        f"/projects/projects/{project['id']}",
    ):
        response = signed_client.get(path)
        assert b"Progress is based on item counts, not money" in response.data
        assert b"1 outstanding / 1 cancelled" in response.data
    assert b"1 tasks outstanding" in signed_client.get("/projects/projects").data
    finish(signed_client, second)
    assert signed_client.get(f"{BASE}/reports").get_json()["progress"]["percent"] == 100
    assert latest(signed_client, project)["status"] == "planned"
    assert latest(signed_client, activity)["status"] == "planned"


def test_status_permissions_and_stale_version(
    signed_client, client, planner_user, viewer_user
):
    task = create(signed_client)
    grant(signed_client, task, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    assert b">Start</button>" not in client.get(f"/projects/tasks/{task['id']}").data
    assert status(client, task, "active").status_code == 403
    sign_in(client, planner_user)
    grant(client, task, user_id=viewer_user.id, role="editor")
    sign_in(client, viewer_user)
    assert b">Start</button>" in client.get(f"/projects/tasks/{task['id']}").data
    assert status(client, task, "active").status_code == 302
    assert status(client, task, "cancelled").status_code == 409
    assert status(client, latest(client, task), "invalid").status_code == 400


def test_html_edit_cannot_bypass_close_validation(signed_client, planner_user):
    project = create(signed_client, "projects")
    create(signed_client, project_id=project["id"])
    status(signed_client, project, "active")
    project = latest(signed_client, project)
    response = signed_client.post(
        f"/projects/projects/{project['id']}/edit",
        data={
            "name": project["name"],
            "department": "Planning",
            "owner_id": planner_user.id,
            "priority": "normal",
            "version": project["version"],
            "status": "completed",
            "budget": "0",
            "actuals": "0",
        },
    )
    assert response.status_code == 409
    assert Project.query.get(project["id"]).status.value == "active"


def test_cancelled_children_allow_parent_close_and_zero_task_progress(signed_client):
    activity = create(signed_client, "activities")
    task = create(signed_client, activity_id=activity["id"])
    blocked = status(signed_client, activity, "cancelled")
    assert blocked.status_code == 302
    assert b"1 tasks remain outstanding" in signed_client.get(blocked.location).data
    assert status(signed_client, task, "cancelled").status_code == 302
    finish(signed_client, activity)
    report = signed_client.get(f"{BASE}/reports").get_json()
    assert report["completion"]["activities"]["percent"] == 100
    assert report["completion"]["tasks"]["outstanding"] == 0
    assert report["progress"]["percent"] == 0


def test_status_forms_require_csrf(signed_client, app):
    import re

    task = create(signed_client)
    previous = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert status(signed_client, task, "active").status_code == 400
        html = signed_client.get("/projects/tasks").data
        token = re.search(rb'name="csrf_token" value="([^"]+)"', html).group(1).decode()
        response = signed_client.post(
            f"/projects/tasks/{task['id']}/status",
            data={
                "status": "active",
                "version": task["version"],
                "csrf_token": token,
            },
        )
        assert response.status_code == 302
        assert latest(signed_client, task)["status"] == "active"
    finally:
        app.config["WTF_CSRF_ENABLED"] = previous
