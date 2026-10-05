"""Contextual creation, location references and read-only scheduling."""

from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from app.extensions import db
from app.projects.models import Activity, Project, Task
from app.projects.scheduling import schedule
from app.projects.services import work_reference
from tests.test_projects import BASE, create, grant, patch, sign_in


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


def test_references_cover_all_contexts_and_never_renumber(signed_client):
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    independent = create(signed_client, "activities")
    nested = create(signed_client, activity_id=activity["id"])
    direct = create(signed_client, project_id=project["id"])
    independent_task = create(signed_client, activity_id=independent["id"])
    standalone = create(signed_client)
    assert project["reference"] == f'{project["id"]:02}'
    assert activity["reference"] == f'{project["id"]:02}.{activity["id"]:02}'
    assert nested["reference"] == f'{activity["reference"]}.{nested["id"]:02}'
    assert direct["reference"] == f'{project["id"]:02}.00.{direct["id"]:02}'
    assert independent["reference"] == f'A{independent["id"]:02}'
    assert independent_task["reference"] == f'{independent["reference"]}.{independent_task["id"]:02}'
    assert standalone["reference"] == f'T{standalone["id"]:02}'
    assert signed_client.delete(
        f"{BASE}/tasks/{nested['id']}", json={"version": nested["version"]}
    ).status_code == 200
    assert signed_client.get(f"{BASE}/tasks/{direct['id']}").get_json()["reference"] == direct["reference"]
    moved = patch(signed_client, direct, project_id=None, activity_id=activity["id"]).get_json()
    assert moved["id"] == direct["id"]
    assert moved["reference"] == f'{activity["reference"]}.{direct["id"]:02}'


def test_references_do_not_truncate_large_record_ids():
    project = Project(id=103)
    activity = Activity(id=105, project=project)
    activity.project_id = project.id
    task = Task(id=107, activity=activity)
    assert work_reference(project) == "103"
    assert work_reference(activity) == "103.105"
    assert work_reference(task) == "103.105.107"


def test_creation_options_require_authentication_and_exclude_closed_parents(
    signed_client, client
):
    closed = create(signed_client, "projects")
    assert patch(signed_client, closed, status="cancelled").status_code == 200
    options = signed_client.get(f"{BASE}/tasks/create-options").get_json()
    assert f"projects:{closed['id']}" not in [row["value"] for row in options["parents"]]
    client.get("/auth/logout")
    assert client.get(f"{BASE}/tasks/create-options").status_code == 401


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_creation_options_defaults_and_forms(signed_client, planner_user, kind):
    response = signed_client.get(f"{BASE}/{kind}/create-options")
    assert response.status_code == 200
    data = response.get_json()
    assert data["defaults"]["owner_id"] == planner_user.id
    assert data["defaults"]["priority"] == "normal"
    assert data["defaults"]["parent"] == ""
    html = signed_client.get(f"/projects/{kind}/new").get_data(as_text=True)
    assert "More details: description, schedule and costs" in html
    assert "Save and add another" in html
    assert "Hold Ctrl" not in html
    if kind == "tasks":
        assert 'name="parent"' in html
        assert 'name="activity_id"' not in html
        assert "data-people-picker" in html


def test_parent_defaults_access_and_closed_parent_options(
    signed_client, client, viewer_user
):
    project = create(signed_client, "projects", department="Engineering")
    activity = create(signed_client, "activities", project_id=project["id"])
    options = signed_client.get(
        f"{BASE}/tasks/create-options?project_id={project['id']}"
    ).get_json()
    assert options["defaults"]["parent"] == f"projects:{project['id']}"
    assert options["defaults"]["department"] == "Engineering"
    html = signed_client.get(
        f"/projects/tasks/new?project_id={project['id']}"
    ).get_data(as_text=True)
    assert 'value="Engineering"' in html
    assert f'value="projects:{project["id"]}" selected' in html
    assert signed_client.get(
        f"{BASE}/tasks/create-options?project_id={project['id']}&activity_id={activity['id']}"
    ).status_code == 400
    grant(signed_client, project, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    data = client.get(f"{BASE}/tasks/create-options").get_json()
    assert data["parents"] == []
    assert client.get(
        f"{BASE}/tasks/create-options?project_id={project['id']}"
    ).status_code == 403


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_save_and_another_retains_parent_and_return(signed_client, planner_user, kind):
    project = create(signed_client, "projects")
    origin = f"/projects/{kind}?view=list"
    data = {
        "name": "Fast work", "department": "Planning", "owner_id": planner_user.id,
        "priority": "normal", "budget": "0", "actuals": "0",
        "save_action": "another", "return_to": origin,
    }
    if kind != "projects":
        data["parent"] = f"projects:{project['id']}"
    response = signed_client.post(f"/projects/{kind}/new", data=data)
    assert response.status_code == 302
    target = urlsplit(response.location)
    assert target.path == f"/projects/{kind}/new"
    query = parse_qs(target.query)
    assert query["return_to"] == [origin]
    if kind != "projects":
        assert query["project_id"] == [str(project["id"])]
    follow = signed_client.get(response.location)
    assert follow.status_code == 200
    assert b"Fast work" not in follow.data


def test_single_parent_form_saves_and_invalid_draft_is_retained(signed_client, planner_user):
    activity = create(signed_client, "activities")
    values = {
        "name": "Child", "department": "Planning", "owner_id": planner_user.id,
        "priority": "normal", "budget": "0", "actuals": "0",
        "parent": f"activities:{activity['id']}",
    }
    response = signed_client.post("/projects/tasks/new", data=values)
    assert response.status_code == 302
    task = db.session.query(Task).one()
    assert task.activity_id == activity["id"] and task.project_id is None
    values.update(start_date="2026-10-02", deadline="2026-10-01")
    response = signed_client.post("/projects/tasks/new", data=values)
    assert response.status_code == 400
    assert b'value="Child"' in response.data
    assert f'value="activities:{activity["id"]}" selected'.encode() in response.data
    assert db.session.query(Task).count() == 1


def test_schedule_geometry_dates_deadlines_and_unscheduled(signed_client):
    project = create(
        signed_client, "projects", start_date="2026-10-01", end_date="2026-10-10",
        deadline="2026-10-12",
    )
    create(signed_client, project_id=project["id"])
    result = schedule([db.session.get(Project, project["id"]), *db.session.query(Task).all()])
    assert result["start"] == "2026-10-01" and result["end"] == "2026-10-12"
    row = result["rows"][0]
    assert row["left"] == 0 and row["width"] == round(10 / 12 * 100, 4)
    assert row["deadline_left"] == round(11 / 12 * 100, 4)
    assert row["has_duration"] is True
    assert len(result["unscheduled"]) == 1
    assert len(result["ticks"]) <= 5


@pytest.mark.parametrize("field", ["deadline", "start_date", "end_date"])
def test_single_date_is_milestone_and_empty_schedule_is_safe(signed_client, field):
    item = create(signed_client, **{field: "2026-10-05"})
    result = schedule([db.session.get(Task, item["id"])])
    assert result["rows"][0]["has_duration"] is False
    assert result["rows"][0]["width"] == 100
    assert result["rows"][0]["left"] == 0
    assert len(result["ticks"]) == 1
    assert schedule([])["rows"] == []


def test_timeline_scopes_children_and_hides_private_and_deleted_work(
    signed_client, client, viewer_user
):
    project = create(signed_client, "projects", name="Visible schedule", deadline="2026-10-20")
    activity = create(signed_client, "activities", name="Activity schedule", project_id=project["id"])
    child = create(signed_client, name="Child schedule", activity_id=activity["id"])
    private = create(signed_client, name="Private schedule", deadline="2026-10-21")
    grant(signed_client, project, user_id=viewer_user.id, role="viewer")
    signed_client.delete(f"{BASE}/tasks/{child['id']}", json={"version": child["version"]})
    sign_in(client, viewer_user)
    for path in (
        "/projects/timeline", "/projects/projects?view=timeline",
        f"/projects/projects/{project['id']}",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert b"Visible schedule" in response.data and b"Activity schedule" in response.data
        assert private["name"].encode() not in response.data
        assert b"Child schedule" not in response.data
    assert client.get(f"{BASE}/tasks/{private['id']}").status_code == 404


def test_schedule_wide_range_has_bounded_ticks(signed_client):
    item = create(signed_client, start_date="0001-01-01", end_date="9999-12-31")
    result = schedule([db.session.get(Task, item["id"])])
    assert len(result["ticks"]) == 5
    assert result["rows"][0]["width"] == 100
    assert all(date.fromisoformat(tick["label"]) for tick in result["ticks"])


def test_schedule_groups_roots_and_children(signed_client):
    first = create(signed_client, "projects", deadline="2026-10-01")
    second = create(signed_client, "projects", deadline="2026-10-01")
    activity = create(signed_client, "activities", project_id=first["id"], deadline="2026-10-01")
    nested = create(signed_client, activity_id=activity["id"], deadline="2026-10-01")
    direct = create(signed_client, project_id=first["id"], deadline="2026-10-01")
    result = schedule([
        *db.session.query(Task).all(), *db.session.query(Project).all(),
        *db.session.query(Activity).all(),
    ])
    assert [row["reference"] for row in result["rows"]] == [
        first["reference"], activity["reference"], nested["reference"],
        direct["reference"], second["reference"],
    ]


def test_today_marker_and_references_follow_existing_move_workflow(signed_client):
    today = date.today()
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    task = create(
        signed_client, activity_id=activity["id"],
        start_date=(today - timedelta(days=1)).isoformat(),
        end_date=(today + timedelta(days=1)).isoformat(),
    )
    result = schedule([db.session.get(Task, task["id"])])
    assert result["today"] == round(100 / 3, 4)
    assert patch(signed_client, activity, project_id=None).status_code == 409
    assert patch(signed_client, task, activity_id=None).status_code == 200
    assert patch(signed_client, activity, project_id=None).status_code == 200
    task = signed_client.get(f"{BASE}/tasks/{task['id']}").get_json()
    assert task["reference"] == f'T{task["id"]:02}'
    assert patch(signed_client, task, activity_id=activity["id"]).status_code == 200
    updated = signed_client.get(f"{BASE}/tasks/{task['id']}").get_json()
    assert updated["reference"] == f'A{activity["id"]:02}.{task["id"]:02}'
