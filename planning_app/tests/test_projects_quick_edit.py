"""Quick edit discovery and existing PATCH validation/access guarantees."""

import pytest
from flask import g

from app.extensions import db
from app.projects.models import LogEntry
from tests.test_projects import BASE, create, grant, patch, sign_in


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


@pytest.mark.parametrize("view", ["list", "hierarchy", "board"])
def test_quick_edit_in_every_task_view(signed_client, view):
    task = create(signed_client)
    html = signed_client.get(f"/projects/tasks?view={view}").get_data(as_text=True)
    assert 'data-quick-edit ' in html
    assert f'data-options-url="{BASE}/tasks/{task["id"]}/edit-options"' in html
    assert f'data-save-url="{BASE}/tasks/{task["id"]}"' in html
    assert f'href="/projects/tasks/{task["id"]}/edit?' in html
    assert html.count("data-quick-edit-dialog") == 1
    assert "data-assignee-search" in html
    assert "remove_unavailable" in html


def test_options_return_current_values_and_only_eligible_users(
    signed_client, planner_user, viewer_user, admin_user
):
    task = create(signed_client)
    path = f"{BASE}/tasks/{task['id']}/edit-options"
    options = signed_client.get(path).get_json()
    assert options["item"] == task
    assert set(options) == {"item", "priorities", "assignees"}
    assert "normal" in options["priorities"]
    assert {user["id"] for user in options["assignees"]} == {
        planner_user.id, admin_user.id
    }
    grant(signed_client, task, user_id=viewer_user.id, role="viewer")
    assert viewer_user.id in {
        user["id"] for user in signed_client.get(path).get_json()["assignees"]
    }
    viewer_user.is_active = False
    db.session.commit()
    assert viewer_user.id not in {
        user["id"] for user in signed_client.get(path).get_json()["assignees"]
    }


def test_options_use_inherited_access(signed_client, viewer_user):
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    task = create(signed_client, activity_id=activity["id"])
    grant(signed_client, project, user_id=viewer_user.id, role="viewer")
    options = signed_client.get(f"{BASE}/tasks/{task['id']}/edit-options").get_json()
    assert viewer_user.id in {user["id"] for user in options["assignees"]}


def test_patch_changes_only_quick_fields_and_audits(signed_client, viewer_user):
    task = create(
        signed_client, description="Keep description", budget="120",
        actuals="15", start_date="2026-10-01"
    )
    grant(signed_client, task, user_id=viewer_user.id, role="viewer")
    before_logs = LogEntry.query.count()
    updated = patch(
        signed_client, task, deadline="2026-10-15", priority="high",
        assigned_user_ids=[viewer_user.id]
    ).get_json()
    assert updated["deadline"] == "2026-10-15"
    assert updated["priority"] == "high"
    assert updated["assigned_user_ids"] == [viewer_user.id]
    assert updated["version"] > task["version"]
    for field in (
        "name", "description", "status", "owner_id", "department",
        "budget", "actuals", "start_date", "project_id", "activity_id"
    ):
        assert updated[field] == task[field]
    assert LogEntry.query.count() == before_logs + 1
    response = patch(signed_client, task, deadline=None)
    assert response.status_code == 409
    cleared = patch(
        signed_client, updated, deadline=None, assigned_user_ids=[]
    ).get_json()
    assert cleared["deadline"] is None and cleared["assigned_user_ids"] == []


def test_invalid_deadline_or_ineligible_assignee_does_not_mutate(
    signed_client, viewer_user
):
    task = create(signed_client, start_date="2026-10-01")
    for values in (
        {"deadline": "2026-09-30"},
        {"priority": "unknown"},
        {"assigned_user_ids": [viewer_user.id]},
    ):
        response = patch(signed_client, task, **values)
        assert response.status_code == 400
        current = signed_client.get(f"{BASE}/tasks/{task['id']}").get_json()
        assert current == task


@pytest.mark.parametrize("shared", [False, True])
def test_viewer_cannot_discover_or_use_quick_edit(
    signed_client, client, viewer_user, shared
):
    task = create(signed_client)
    if shared:
        grant(signed_client, task, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    for view in ("list", "hierarchy", "board"):
        html = client.get(f"/projects/tasks?view={view}").get_data(as_text=True)
        assert 'data-quick-edit ' not in html
    response = client.get(f"{BASE}/tasks/{task['id']}/edit-options")
    assert response.status_code == (403 if shared else 404)
    assert patch(client, task, priority="high").status_code == (403 if shared else 404)


def test_editor_can_use_options_and_patch(signed_client, client, viewer_user):
    task = create(signed_client)
    grant(signed_client, task, user_id=viewer_user.id, role="editor")
    sign_in(client, viewer_user)
    assert client.get(f"{BASE}/tasks/{task['id']}/edit-options").status_code == 200
    assert b"data-quick-edit " in client.get("/projects/tasks?view=board").data
    assert patch(client, task, priority="high").status_code == 200


def test_deleted_task_and_anonymous_options_are_unavailable(signed_client, client):
    task = create(signed_client)
    signed_client.delete(
        f"{BASE}/tasks/{task['id']}", json={"version": task["version"]}
    )
    assert signed_client.get(f"{BASE}/tasks/{task['id']}/edit-options").status_code == 404
    with client.session_transaction() as session:
        session.clear()
    g.pop("_login_user", None)
    assert client.get(f"{BASE}/tasks/{task['id']}/edit-options").status_code == 401


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
@pytest.mark.parametrize("view", ["list", "hierarchy"])
def test_all_work_kinds_offer_quick_edit(signed_client, kind, view):
    item = create(signed_client, kind)
    for path in (
        f"/projects/{kind}?view={view}",
        f"/projects/{kind}/{item['id']}",
        "/projects/",
    ):
        html = signed_client.get(path).get_data(as_text=True)
        assert f'data-options-url="{BASE}/{kind}/{item["id"]}/edit-options"' in html
        assert f'data-save-url="{BASE}/{kind}/{item["id"]}"' in html
    response = signed_client.get(f"{BASE}/{kind}/{item['id']}/edit-options")
    assert response.status_code == 200
    assert response.get_json()["item"] == item
    if kind != "tasks":
        assert response.get_json()["assignees"] == []


@pytest.mark.parametrize("kind", ["projects", "activities"])
def test_parent_quick_patch_preserves_children_and_unrelated_fields(signed_client, kind):
    item = create(
        signed_client, kind, start_date="2026-10-01",
        description="Unchanged", budget="10"
    )
    child = create(
        signed_client,
        **{"project_id" if kind == "projects" else "activity_id": item["id"]},
    )
    updated = patch(
        signed_client, item, deadline="2026-10-20", priority="high"
    ).get_json()
    assert updated["deadline"] == "2026-10-20" and updated["priority"] == "high"
    for field in ("name", "description", "status", "owner_id", "budget"):
        assert updated[field] == item[field]
    assert signed_client.get(f"{BASE}/tasks/{child['id']}").get_json() == child
    assert patch(signed_client, item, priority="low").status_code == 409
    assert patch(signed_client, updated, deadline="2026-09-30").status_code == 400
    assert signed_client.get(f"{BASE}/{kind}/{item['id']}").get_json() == updated


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_every_kind_enforces_viewer_and_editor_access(
    signed_client, client, planner_user, viewer_user, kind
):
    item = create(signed_client, kind)
    path = f"{BASE}/{kind}/{item['id']}/edit-options"
    grant(signed_client, item, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    assert client.get(path).status_code == 403
    assert 'data-quick-edit ' not in client.get(f"/projects/{kind}").get_data(as_text=True)
    assert patch(client, item, priority="high").status_code == 403
    sign_in(client, planner_user)
    grant(client, item, user_id=viewer_user.id, role="editor")
    sign_in(client, viewer_user)
    assert client.get(path).status_code == 200
    assert 'data-quick-edit ' in client.get(f"/projects/{kind}").get_data(as_text=True)
    assert patch(client, item, priority="high").status_code == 200


def test_permission_cache_distinguishes_same_id_across_kinds(
    signed_client, client, viewer_user
):
    project = create(signed_client, "projects", name="Read only project")
    activity = create(signed_client, "activities", name="Editable activity")
    task = create(signed_client, name="Read only task")
    assert project["id"] == activity["id"] == task["id"]
    for item, role in ((project, "viewer"), (activity, "editor"), (task, "viewer")):
        grant(signed_client, item, user_id=viewer_user.id, role=role)
    sign_in(client, viewer_user)
    html = client.get("/projects/").get_data(as_text=True)
    assert f'data-options-url="{BASE}/activities/{activity["id"]}/edit-options"' in html
    assert f'data-options-url="{BASE}/projects/{project["id"]}/edit-options"' not in html
    assert f'data-options-url="{BASE}/tasks/{task["id"]}/edit-options"' not in html


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_quick_edit_follows_start_before_red_cancel(signed_client, kind):
    item = create(signed_client, kind)
    for path in (f"/projects/{kind}", f"/projects/{kind}/{item['id']}"):
        html = signed_client.get(path).get_data(as_text=True)
        row = html.split(f'aria-label="Status actions for {item["name"]}">')[1].split(
            "</div>", 1
        )[0]
        assert row.index(">Start</button>") < row.index("data-quick-edit")
        assert row.index("data-quick-edit") < row.index(">Cancel / close</button>")
        assert 'class="btn btn-sm btn-outline-danger">Cancel / close</button>' in row
        assert row.count("data-quick-edit ") == 1
    html = signed_client.get(f"/projects/{kind}/{item['id']}").get_data(as_text=True)
    summary = html.split('<section aria-label="Change work status">')[0]
    assert "data-quick-edit " not in summary
