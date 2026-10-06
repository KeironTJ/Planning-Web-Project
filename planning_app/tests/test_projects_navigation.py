"""Return navigation preserves work context without bypassing validation."""

import re
from html import escape, unescape
from urllib.parse import parse_qs, urlsplit

import pytest

from app.projects.models import LogEntry, Task
from tests.test_projects import BASE, create, grant, sign_in


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


def edit_data(item, user, **values):
    return {
        "name": item["name"],
        "department": item["department"],
        "owner_id": user.id,
        "priority": item["priority"],
        "status": item["status"],
        "version": item["version"],
        "budget": item["budget"],
        "actuals": item["actuals"],
        **values,
    }


def test_workspace_hooks_and_status_return(signed_client, planner_user):
    project = create(signed_client, "projects")
    task = create(signed_client, project_id=project["id"])
    origin = "/projects/tasks?status=planned&per_page=1&page=1"
    html = signed_client.get(origin).get_data(as_text=True)
    assert f'data-work-user="{planner_user.id}"' in html
    assert 'type="module"' in html and "js/modules/projects.js" in html
    assert "data-work-filters" in html and "data-work-reset" in html
    assert f'data-state-key="projects:{project["id"]}"' in html
    assert f'name="return_to" value="{escape(origin, quote=True)}"' in html
    response = signed_client.post(
        f"/projects/tasks/{task['id']}/status",
        data={"version": task["version"], "status": "active", "return_to": origin},
    )
    assert response.status_code == 302
    assert response.location == origin
    assert signed_client.get(response.location).status_code == 200
    assert Task.query.get(task["id"]).status.value == "active"


def test_edit_and_cancel_retain_origin(signed_client, planner_user):
    task = create(signed_client)
    origin = "/projects/tasks?scope=mine&per_page=1&page=2"
    detail = signed_client.get(
        f"/projects/tasks/{task['id']}", query_string={"return_to": origin}
    ).get_data(as_text=True)
    assert f'href="{escape(origin, quote=True)}"' in detail
    assert "Back to previous view" in detail
    edit_links = [
        urlsplit(unescape(link))
        for link in re.findall(r'href="([^"]+)"', detail)
        if urlsplit(unescape(link)).path.endswith("/edit")
    ]
    assert parse_qs(edit_links[0].query)["return_to"] == [origin]
    path = f"/projects/tasks/{task['id']}/edit"
    html = signed_client.get(path, query_string={"return_to": origin}).get_data(
        as_text=True
    )
    assert f'name="return_to" value="{escape(origin, quote=True)}"' in html
    assert f'href="{escape(origin, quote=True)}">Cancel</a>' in html
    invalid = signed_client.post(
        path, data=edit_data(task, planner_user, name="", return_to=origin)
    )
    assert invalid.status_code == 400
    assert (
        f'name="return_to" value="{escape(origin, quote=True)}"'
        in invalid.get_data(as_text=True)
    )
    response = signed_client.post(
        path, data=edit_data(task, planner_user, name="Updated task", return_to=origin)
    )
    assert response.status_code == 302 and response.location == origin
    assert Task.query.get(task["id"]).name == "Updated task"


def test_new_item_returns_to_parent(signed_client, planner_user):
    project = create(signed_client, "projects")
    origin = f"/projects/projects/{project['id']}"
    response = signed_client.post(
        "/projects/tasks/new",
        data={
            "name": "New child",
            "department": "Planning",
            "owner_id": planner_user.id,
            "priority": "normal",
            "budget": "0",
            "actuals": "0",
            "project_id": project["id"],
            "return_to": origin,
        },
    )
    assert response.status_code == 302 and response.location == origin
    assert Task.query.one().project_id == project["id"]


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_quick_creation_handoff_previews_without_saving(signed_client, planner_user, kind):
    project = create(signed_client, "projects")
    origin = "/projects/tasks?view=hierarchy"
    before = LogEntry.query.count()
    values = {
        "form_action": "preview", "return_to": origin,
        "name": "Unsaved draft", "description": "Draft <handover>\nNext steps",
        "department": "Planning", "owner_id": planner_user.id,
        "priority": "high", "deadline": "31/02/2026",
        "parent": f"projects:{project['id']}" if kind != "projects" else "",
        "id": "999", "status": "completed", "budget": "999",
    }
    response = signed_client.post(f"/projects/{kind}/new", data=values)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'value="Unsaved draft"' in html
    assert "Draft &lt;handover&gt;\nNext steps" in html
    assert 'value="31/02/2026"' in html
    assert 'value="high" selected' in html
    assert f'href="{escape(origin, quote=True)}">Cancel</a>' in html
    assert 'name="version"' not in html
    assert 'value="999"' not in html
    if kind != "projects":
        assert f'value="projects:{project["id"]}" selected' in html
    assert LogEntry.query.count() == before
    assert signed_client.get(f"{BASE}/{kind}").get_json()["total"] == (
        1 if kind == "projects" else 0
    )
    values.update(deadline="07/10/2026", budget="0", actuals="0", status="planned")
    values.pop("form_action")
    response = signed_client.post(f"/projects/{kind}/new", data=values)
    assert response.status_code == 302 and response.location == origin
    items = signed_client.get(f"{BASE}/{kind}").get_json()["items"]
    saved = next(item for item in items if item["name"] == "Unsaved draft")
    assert saved["description"] == "Draft <handover>\nNext steps"
    assert saved["deadline"] == "2026-10-07"


def test_quick_edit_handoff_keeps_version_assignments_and_context(
    signed_client, planner_user
):
    task = create(
        signed_client, description="Stored description", budget="25",
        start_date="2026-10-01", end_date="2026-10-03",
    )
    newer = signed_client.patch(
        f"{BASE}/tasks/{task['id']}",
        json={"version": task["version"], "priority": "high"},
    ).get_json()
    before = LogEntry.query.count()
    origin = "/projects/tasks?view=list"
    values = {
        "form_action": "preview", "return_to": origin, "version": task["version"],
        "description": "Unsaved edit", "deadline": "09/10/2026", "priority": "low",
        "assigned_user_ids": [planner_user.id],
    }
    path = f"/projects/tasks/{task['id']}/edit"
    response = signed_client.post(path, data=values)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Unsaved edit</textarea>" in html
    assert 'value="09/10/2026"' in html
    assert f'name="version" value="{task["version"]}"' in html
    assert f'value="{planner_user.id}" checked' in html
    assert f'name="name"' in html and f'value="{task["name"]}"' in html
    assert 'value="25.00"' in html
    assert 'value="01/10/2026"' in html and 'value="03/10/2026"' in html
    assert LogEntry.query.count() == before
    assert signed_client.get(f"{BASE}/tasks/{task['id']}").get_json() == newer
    values.update(edit_data(task, planner_user, description="Unsaved edit", return_to=origin))
    values.pop("form_action")
    assert signed_client.post(path, data=values).status_code == 409
    assert signed_client.get(f"{BASE}/tasks/{task['id']}").get_json() == newer


def test_quick_edit_handoff_still_requires_edit_access(signed_client, client, viewer_user):
    task = create(signed_client)
    grant(signed_client, task, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    assert client.post(
        f"/projects/tasks/{task['id']}/edit",
        data={"form_action": "preview", "description": "Not permitted"},
    ).status_code == 403


def test_full_form_handoff_requires_csrf(signed_client, app):
    previous = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert signed_client.post(
            "/projects/tasks/new",
            data={"form_action": "preview", "name": "Draft without CSRF"},
        ).status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = previous


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.invalid/projects/tasks",
        "//example.invalid/projects/tasks",
        "/%2fexample.invalid/projects/tasks",
        "%2fprojects/tasks",
        "/projects\\tasks",
        "/projects/%5ctasks",
        "/projects/tasks%0d%0aLocation:evil",
        "/auth/login",
        "/projects/api/tasks",
        "/projects/tasks/new",
        "/projects/tasks/1/edit",
        "/projects/tasks/1/status",
        "/projects/unknown",
    ],
)
def test_invalid_return_is_rejected_before_mutation(signed_client, origin):
    task = create(signed_client)
    before = LogEntry.query.count()
    response = signed_client.post(
        f"/projects/tasks/{task['id']}/status",
        data={"version": task["version"], "status": "active", "return_to": origin},
    )
    assert response.status_code == 400
    assert response.location is None
    assert Task.query.get(task["id"]).status.value == "planned"
    assert LogEntry.query.count() == before


def test_comment_returns_to_logs_with_context(signed_client):
    task = create(signed_client)
    origin = f"/projects/tasks/{task['id']}?return_to=%2Fprojects%2Ftasks#logs"
    response = signed_client.post(
        "/projects/logs",
        data={"task_id": task["id"], "body": "Keep context", "return_to": origin},
    )
    assert response.status_code == 302 and response.location == origin
    log = LogEntry.query.filter_by(kind="comment").one()
    response = signed_client.post(
        f"/projects/logs/{log.id}/edit",
        data={"version": log.version, "body": "Updated comment", "return_to": origin},
    )
    assert response.status_code == 302 and response.location == origin
    response = signed_client.post(
        f"/projects/logs/{log.id}/delete",
        data={"version": log.version, "return_to": origin},
    )
    assert response.status_code == 302 and response.location == origin


def test_sharing_retains_detail_context(signed_client, viewer_user):
    task = create(signed_client)
    origin = f"/projects/tasks/{task['id']}?return_to=%2Fprojects%2Ftasks"
    response = signed_client.post(
        f"/projects/tasks/{task['id']}/shares",
        data={"user_id": viewer_user.id, "role": "viewer", "return_to": origin},
    )
    assert response.status_code == 302 and response.location == origin
    share = signed_client.get(f"{BASE}/tasks/{task['id']}/shares").get_json()["items"][0]
    response = signed_client.post(
        f"/projects/tasks/{task['id']}/shares/{share['id']}/delete",
        data={"return_to": origin},
    )
    assert response.status_code == 302 and response.location == origin


def test_return_destination_does_not_bypass_access(signed_client, client, viewer_user):
    task = create(signed_client)
    grant(signed_client, task, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    response = client.post(
        f"/projects/tasks/{task['id']}/status",
        data={
            "version": task["version"],
            "status": "active",
            "return_to": "/projects/tasks",
        },
    )
    assert response.status_code == 403
    assert Task.query.get(task["id"]).status.value == "planned"
    assert client.get(f"{BASE}/tasks/{task['id']}").get_json()["status"] == "planned"


def test_direct_edit_and_stale_status_keep_existing_behaviour(signed_client, planner_user):
    task = create(signed_client)
    detail = f"/projects/tasks/{task['id']}"
    response = signed_client.post(
        detail + "/edit", data=edit_data(task, planner_user, name="Direct edit")
    )
    assert response.status_code == 302 and response.location == detail
    response = signed_client.post(
        detail + "/status",
        data={
            "version": task["version"],
            "status": "active",
            "return_to": "/projects/tasks?status=planned",
        },
    )
    assert response.status_code == 409
    assert b"Back to previous view" in response.data
    assert Task.query.get(task["id"]).status.value == "planned"
