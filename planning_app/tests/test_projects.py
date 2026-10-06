"""Project tracking integration tests against isolated SQLite databases."""

import importlib
from datetime import date, timedelta

import pytest
from flask import g
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.auth.models import AuditLog
from app.projects.models import Activity, LogEntry, Project, Task

BASE = "/projects/api"


def sign_in(client, user):
    g.pop("_login_user", None)
    with client.session_transaction() as session:
        session.clear()
    password = {
        "planner_test": "Planner!Pass1234",
        "viewer_test": "Viewer!Pass1234",
        "admin_test": "Admin!Pass1234",
    }[user.username]
    response = client.post(
        "/auth/login", data={"login": user.email, "password": password}
    )
    assert response.status_code == 302


def create(client, kind="tasks", **values):
    response = client.post(
        f"{BASE}/{kind}",
        json={
            "name": f"Factory {kind}",
            "department": "Planning",
            **values,
        },
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


def patch(client, item, **values):
    return client.patch(
        f"{BASE}/{item['kind']}/{item['id']}",
        json={
            "version": item["version"],
            **values,
        },
    )


def grant(client, item, **values):
    response = client.post(f"{BASE}/{item['kind']}/{item['id']}/shares", json=values)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["items"]


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


def test_all_hierarchy_combinations_and_summaries(signed_client):
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    standalone = create(signed_client, "activities")
    tasks = [
        create(signed_client),
        create(signed_client, project_id=project["id"]),
        create(signed_client, activity_id=activity["id"]),
        create(signed_client, activity_id=standalone["id"]),
    ]
    assert tasks[2]["project_id"] is None
    assert tasks[2]["effective_project_id"] == project["id"]
    assert tasks[3]["effective_project_id"] is None
    assert (
        patch(
            signed_client,
            tasks[0],
            activity_id=activity["id"],
            project_id=project["id"],
        ).status_code
        == 400
    )
    summary = signed_client.get(f"{BASE}/projects/{project['id']}/summary").get_json()
    assert summary["progress"] == {"completed": 0, "total": 2, "percent": 0}
    assert len(summary["children"]) == 3
    assert len(summary["timeline"]) == 4


def test_private_items_never_leak(client, signed_client, viewer_user):
    project = create(signed_client, "projects", name="Private project")
    activity = create(signed_client, "activities", project_id=project["id"])
    task = create(signed_client, activity_id=activity["id"])
    sign_in(client, viewer_user)
    for item in (project, activity, task):
        assert client.get(f"{BASE}/{item['kind']}/{item['id']}").status_code == 404
    for path in ("/projects/", "/projects/reports", "/"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"Private project" not in response.data
    assert client.get(f"{BASE}/tasks").get_json()["items"] == []
    assert client.get(f"{BASE}/logs").get_json()["items"] == []
    assert client.get(f"{BASE}/reports").get_json()["timeline"] == []


def test_viewer_editor_inheritance_and_revocation(
    client, signed_client, planner_user, viewer_user
):
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    task = create(signed_client, activity_id=activity["id"])
    share = grant(signed_client, project, user_id=viewer_user.id, role="viewer")[0]
    sign_in(client, viewer_user)
    assert client.get(f"{BASE}/tasks/{task['id']}").status_code == 200
    assert patch(client, task, name="Not allowed").status_code == 403
    assert (
        client.post(
            f"{BASE}/logs", json={"task_id": task["id"], "body": "No"}
        ).status_code
        == 403
    )
    sign_in(client, planner_user)
    grant(client, project, user_id=viewer_user.id, role="editor")
    sign_in(client, viewer_user)
    changed = patch(client, task, name="Editor updated")
    assert changed.status_code == 200
    assert (
        client.post(
            f"{BASE}/projects/{project['id']}/shares",
            json={
                "user_id": viewer_user.id,
                "role": "editor",
            },
        ).status_code
        == 403
    )
    assert patch(client, task, project_id=None, activity_id=None).status_code == 409
    latest = changed.get_json()
    assert patch(client, latest, activity_id=None).status_code == 403
    assert patch(client, latest, owner_id=viewer_user.id).status_code == 403
    assert (
        client.delete(
            f"{BASE}/tasks/{task['id']}",
            json={
                "version": latest["version"],
            },
        ).status_code
        == 403
    )
    sign_in(client, planner_user)
    assert (
        client.delete(
            f"{BASE}/projects/{project['id']}/shares/{share['id']}"
        ).status_code
        == 204
    )
    sign_in(client, viewer_user)
    assert client.get(f"{BASE}/tasks/{task['id']}").status_code == 404
    assert client.get(f"{BASE}/reports").get_json()["timeline"] == []


def test_department_share_and_child_cannot_widen_visibility(
    signed_client, client, viewer_user, db
):
    viewer_user.department = "Operations"
    db.session.commit()
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    grant(signed_client, project, department="Operations", role="viewer")
    assert (
        signed_client.post(
            f"{BASE}/activities/{activity['id']}/shares",
            json={
                "user_id": viewer_user.id,
                "role": "editor",
            },
        ).status_code
        == 403
    )
    sign_in(client, viewer_user)
    assert client.get(f"{BASE}/activities/{activity['id']}").status_code == 200
    assert len(client.get(f"{BASE}/activities").get_json()["items"]) == 1


def test_standalone_sharing(signed_client, client, viewer_user):
    activity = create(signed_client, "activities")
    task = create(signed_client, activity_id=activity["id"])
    direct_task = create(signed_client)
    grant(signed_client, activity, user_id=viewer_user.id, role="viewer")
    grant(signed_client, direct_task, user_id=viewer_user.id, role="editor")
    sign_in(client, viewer_user)
    assert len(client.get(f"{BASE}/tasks").get_json()["items"]) == 2
    assert patch(client, task, name="Denied").status_code == 403
    assert patch(client, direct_task, name="Allowed").status_code == 200


@pytest.mark.parametrize(
    "values",
    [
        {"name": ""},
        {"department": ""},
        {"name": "x" * 201},
        {"status": "completed"},
        {"priority": "invalid"},
        {"start_date": "2026-10-10", "end_date": "2026-10-09"},
        {"start_date": "2026-10-10", "deadline": "2026-10-09"},
        {"start_date": "garbage"},
        {"start_date": False},
        {"budget": "-1"},
        {"budget": "NaN"},
        {"actuals": "Infinity"},
        {"budget": "1.001"},
        {"budget": "1e1000"},
        {"owner_id": True},
        {"assigned_user_ids": "not a list"},
        {"activity_id": 999999},
        {"unknown_field": 1},
    ],
)
def test_validation_is_atomic(signed_client, values):
    expected_status = 404 if "activity_id" in values else 400
    assert (
        signed_client.post(
            f"{BASE}/tasks",
            json={
                "name": "Test",
                "department": "Planning",
                **values,
            },
        ).status_code
        == expected_status
    )
    assert Task.query.count() == 0
    assert LogEntry.query.count() == 0
    assert AuditLog.query.filter(AuditLog.action.like("projects.%")).count() == 0


def test_status_transitions_budget_variance_and_version(signed_client):
    task = create(signed_client, budget="10.00", actuals="12.50")
    assert task["variance"] == "-2.50"
    assert task["over_budget"]
    assert patch(signed_client, task, status="completed").status_code == 400
    active = patch(signed_client, task, status="active").get_json()
    assert active["version"] > task["version"]
    assert patch(signed_client, task, name="Stale overwrite").status_code == 409
    blocked = patch(signed_client, active, status="blocked").get_json()
    assert patch(signed_client, blocked, status="completed").status_code == 400
    active = patch(signed_client, blocked, status="active").get_json()
    complete = patch(signed_client, active, status="completed").get_json()
    assert complete["status"] == "completed"
    assert (
        signed_client.patch(
            f"{BASE}/tasks/{task['id']}", json={"name": "No version"}
        ).status_code
        == 400
    )


def test_assignment_needs_access_and_workload(
    signed_client, client, planner_user, viewer_user
):
    task = create(signed_client)
    assert (
        patch(signed_client, task, assigned_user_ids=[viewer_user.id]).status_code
        == 400
    )
    grant(signed_client, task, user_id=viewer_user.id, role="viewer")
    assigned = patch(
        signed_client,
        task,
        assigned_user_ids=[planner_user.id, viewer_user.id, viewer_user.id],
    ).get_json()
    assert len(assigned["assigned_user_ids"]) == 2
    assert assigned["version"] > task["version"]
    assert patch(signed_client, task, assigned_user_ids=[]).status_code == 409
    workload = signed_client.get(f"{BASE}/reports").get_json()["workload"]
    assert sorted(row["open_tasks"] for row in workload) == [1, 1]
    sign_in(client, viewer_user)
    assert client.get(f"{BASE}/tasks/{task['id']}").status_code == 200


def test_assignment_cannot_borrow_unrelated_share(signed_client, viewer_user):
    unrelated = create(signed_client, "activities")
    grant(signed_client, unrelated, user_id=viewer_user.id, role="viewer")
    response = signed_client.post(
        f"{BASE}/tasks",
        json={
            "name": "Private task",
            "department": "Planning",
            "assigned_user_ids": [viewer_user.id],
        },
    )
    assert response.status_code == 400


def test_log_crud_and_immutable_audit(signed_client):
    task = create(signed_client)
    response = signed_client.post(
        f"{BASE}/logs", json={"task_id": task["id"], "body": "Initial"}
    )
    assert response.status_code == 201
    log = response.get_json()
    changed = signed_client.patch(
        f"{BASE}/logs/{log['id']}",
        json={
            "version": log["version"],
            "body": "Revised",
        },
    ).get_json()
    assert changed["body"] == "Revised"
    audit = LogEntry.query.filter_by(kind="audit").first()
    assert (
        signed_client.patch(
            f"{BASE}/logs/{audit.id}",
            json={
                "version": audit.version,
                "body": "Tamper",
            },
        ).status_code
        == 403
    )
    assert (
        signed_client.delete(
            f"{BASE}/logs/{log['id']}",
            json={
                "version": changed["version"],
            },
        ).status_code
        == 200
    )
    assert signed_client.get(f"{BASE}/logs/{log['id']}").status_code == 404
    history = signed_client.get(f"{BASE}/tasks/{task['id']}/history").get_json()[
        "timeline"
    ]
    assert any(row["body"] == "Revised" and row["deleted_at"] for row in history)
    assert any(
        '"body": "Initial"' in row["body"] for row in history if row["kind"] == "audit"
    )


def test_log_requires_exactly_one_target(signed_client):
    project = create(signed_client, "projects")
    task = create(signed_client)
    for data in (
        {"body": "No target"},
        {"body": "Too many", "project_id": project["id"], "task_id": task["id"]},
        {"task_id": task["id"], "body": ""},
    ):
        assert signed_client.post(f"{BASE}/logs", json=data).status_code == 400


def test_soft_delete_child_protection_and_history(signed_client, client, viewer_user):
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    task = create(signed_client, activity_id=activity["id"])
    assert (
        signed_client.delete(
            f"{BASE}/projects/{project['id']}",
            json={
                "version": project["version"],
            },
        ).status_code
        == 409
    )
    for item in (task, activity, project):
        assert (
            signed_client.delete(
                f"{BASE}/{item['kind']}/{item['id']}",
                json={
                    "version": item["version"],
                },
            ).status_code
            == 200
        )
    assert Project.query.count() == Activity.query.count() == Task.query.count() == 1
    assert signed_client.get(f"{BASE}/projects/{project['id']}").status_code == 404
    history = signed_client.get(f"{BASE}/projects/{project['id']}/history").get_json()
    assert len(history["timeline"]) == 6
    assert signed_client.get("/projects/archive").status_code == 200
    sign_in(client, viewer_user)
    assert client.get(f"{BASE}/projects/{project['id']}/history").status_code == 404


def test_reporting_filters_and_progress(signed_client, planner_user):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    activity = create(signed_client, "activities")
    overdue = create(signed_client, deadline=yesterday, budget="10.00", actuals="15.00")
    create(signed_client, deadline=tomorrow, activity_id=activity["id"])
    complete = create(signed_client, deadline=yesterday, activity_id=activity["id"])
    active = patch(signed_client, complete, status="active").get_json()
    patch(signed_client, active, status="completed")
    cancelled = create(signed_client)
    patch(signed_client, cancelled, status="cancelled")
    report = signed_client.get(f"{BASE}/reports").get_json()
    assert report["overdue_tasks"] == [overdue]
    assert len(report["upcoming"]) == 1
    assert report["progress"] == {"completed": 1, "total": 3, "percent": 33.3}
    assert report["activities"][0]["progress"]["percent"] == 50
    assert report["variance"] == "-5.00"
    assert (
        len(
            signed_client.get(
                f"{BASE}/tasks?scope=standalone&deadline=overdue"
            ).get_json()["items"]
        )
        == 1
    )
    assert (
        len(signed_client.get(f"{BASE}/tasks?scope=activity").get_json()["items"]) == 2
    )
    assert (
        len(
            signed_client.get(f"{BASE}/tasks?owner_id={planner_user.id}").get_json()[
                "items"
            ]
        )
        == 4
    )
    assert signed_client.get(f"{BASE}/tasks?status=invalid").status_code == 400


def test_html_pages_and_forms(signed_client, planner_user):
    project = create(signed_client, "projects")
    for path in (
        "/",
        "/projects/",
        "/projects/projects",
        "/projects/activities",
        "/projects/tasks?scope=standalone",
        "/projects/reports",
        "/projects/archive",
        "/projects/projects/new",
        "/projects/activities/new",
        "/projects/tasks/new",
        f"/projects/projects/{project['id']}",
        f"/projects/projects/{project['id']}/edit",
        f"/projects/projects/{project['id']}/history",
    ):
        response = signed_client.get(path)
        assert response.status_code == 200, (path, response.get_data(as_text=True))
    response = signed_client.post(
        "/projects/tasks/new",
        data={
            "name": "HTML task",
            "description": "Form",
            "department": "Planning",
            "owner_id": planner_user.id,
            "priority": "high",
            "budget": "20",
            "actuals": "10",
        },
    )
    assert response.status_code == 302
    assert b"HTML task" in signed_client.get(response.location).data
    response = signed_client.post(
        "/projects/tasks/new",
        data={
            "name": "Invalid",
            "department": "Planning",
            "owner_id": planner_user.id,
            "priority": "normal",
            "budget": "-1",
            "actuals": "0",
        },
    )
    assert response.status_code == 400
    assert b"non-negative" in response.data


def test_api_authentication_and_csrf(client, app, planner_user):
    assert client.get(f"{BASE}/reports").status_code == 401
    sign_in(client, planner_user)
    old = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert (
            client.post(
                f"{BASE}/tasks",
                json={
                    "name": "Missing token",
                    "department": "Planning",
                },
            ).status_code
            == 400
        )
        response = client.get("/projects/tasks/new")
        import re

        token = (
            re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
            .group(1)
            .decode()
        )
        assert (
            client.post(
                f"{BASE}/tasks",
                json={
                    "name": "With token",
                    "department": "Planning",
                },
                headers={"X-CSRFToken": token},
            ).status_code
            == 201
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = old


def test_migration_round_trip_matches_models(db):
    migration = importlib.import_module(
        "migrations.versions.e2a9417c630b_add_project_tracking"
    )
    subtasks_migration = importlib.import_module(
        "migrations.versions.4b8d1a2f6c90_add_task_subtasks"
    )
    connection = db.session.connection()
    tables = (
        "project_logs",
        "project_shares",
        "project_task_assignees",
        "project_tasks",
        "project_activities",
        "projects",
    )
    for table in tables:
        db.metadata.tables[table].drop(connection)
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.upgrade()
        subtasks_migration.upgrade()
        differences = compare_metadata(context, db.metadata)
        assert differences == []
        subtasks_migration.downgrade()
        migration.downgrade()
        migration.upgrade()
        subtasks_migration.upgrade()
    assert db.session.query(Project).count() == 0


def test_database_optimistic_locking(signed_client, db):
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.exc import StaleDataError

    item = create(signed_client)
    first = db.session.get(Task, item["id"])
    with Session(bind=db.engine) as second_session:
        second = second_session.get(Task, item["id"])
        first.name = "First change"
        db.session.commit()
        second.name = "Lost update"
        with pytest.raises(StaleDataError):
            second_session.commit()
    assert db.session.get(Task, item["id"]).name == "First change"


def test_pagination_and_query_validation(signed_client):
    for index in range(3):
        create(signed_client, name=f"Task {index}")
    first = signed_client.get(f"{BASE}/tasks?per_page=2").get_json()
    assert first["total"] == 3
    assert first["page"] == 1
    assert first["per_page"] == 2
    assert first["pages"] == 2
    second = signed_client.get(f"{BASE}/tasks?per_page=2&page=2").get_json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert {row["id"] for row in first["items"]}.isdisjoint(
        {row["id"] for row in second["items"]}
    )
    logs = signed_client.get(f"{BASE}/logs?per_page=2").get_json()
    assert logs["total"] == 3 and len(logs["items"]) == 2
    for query in (
        "page=0",
        "per_page=101",
        "owner_id=bad",
        "owner_id=999999999999999999999999",
        "scope=invalid",
        "deadline=invalid",
        "priority=invalid",
    ):
        assert signed_client.get(f"{BASE}/tasks?{query}").status_code == 400
    response = signed_client.get("/projects/tasks?per_page=2&kind=ignored")
    assert response.status_code == 200
    assert b"Next" in response.data
    assert len(signed_client.get(f"{BASE}/tasks?scope=mine").get_json()["items"]) == 3


def test_effective_project_filters_and_due_home(signed_client):
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    direct = create(signed_client, project_id=project["id"])
    nested = create(signed_client, activity_id=activity["id"])
    for index in range(7):
        create(
            signed_client,
            name=f"Overdue {index}",
            deadline=(date.today() - timedelta(days=index + 1)).isoformat(),
        )
    matched = signed_client.get(f"{BASE}/tasks?project_id={project['id']}").get_json()
    assert {row["id"] for row in matched["items"]} == {direct["id"], nested["id"]}
    assert (
        len(signed_client.get(f"{BASE}/tasks?scope=project").get_json()["items"]) == 2
    )
    home = signed_client.get("/")
    assert b"Overdue (7)" in home.data
    assert b"Overdue 6" in home.data and b"Overdue 0" not in home.data


def test_editor_created_child_stays_private_after_revocation(
    signed_client,
    client,
    planner_user,
    viewer_user,
):
    project = create(signed_client, "projects")
    share = grant(signed_client, project, user_id=viewer_user.id, role="editor")[0]
    sign_in(client, viewer_user)
    own_child = create(client, project_id=project["id"])
    sign_in(client, planner_user)
    client.delete(f"{BASE}/projects/{project['id']}/shares/{share['id']}")
    sign_in(client, viewer_user)
    assert client.get(f"{BASE}/tasks/{own_child['id']}").status_code == 404
    assert client.get(f"{BASE}/tasks").get_json()["items"] == []


def test_html_comment_sharing_and_deletion(signed_client, viewer_user):
    task = create(signed_client)
    path = f"/projects/tasks/{task['id']}"
    response = signed_client.post(
        f"{path}/shares",
        data={
            "user_id": viewer_user.id,
            "department": "",
            "role": "viewer",
        },
    )
    assert response.status_code == 302
    response = signed_client.post(
        "/projects/logs",
        data={
            "task_id": task["id"],
            "body": "HTML comment",
        },
    )
    assert response.status_code == 302
    log = LogEntry.query.filter_by(kind="comment").one()
    response = signed_client.post(
        f"/projects/logs/{log.id}/edit",
        data={
            "version": log.version,
            "body": "Edited HTML comment",
        },
    )
    assert response.status_code == 302
    assert b"Edited HTML comment" in signed_client.get(path).data
    response = signed_client.post(
        f"/projects/logs/{log.id}/delete",
        data={
            "version": log.version,
        },
    )
    assert response.status_code == 302
    response = signed_client.post(f"{path}/delete", data={"version": task["version"]})
    assert response.status_code == 302
    assert signed_client.get(path).status_code == 404


def test_hierarchy_cost_rollups_and_utc_timestamps(signed_client):
    project = create(signed_client, "projects", budget="100.00", actuals="50.00")
    activity = create(
        signed_client,
        "activities",
        project_id=project["id"],
        budget="20.00",
        actuals="25.00",
    )
    task = create(
        signed_client, activity_id=activity["id"], budget="10.00", actuals="5.00"
    )
    create(signed_client, project_id=project["id"], budget="5.00")
    assert task["created_at"].endswith("+00:00")
    assert task["updated_at"].endswith("+00:00")
    summary = signed_client.get(f"{BASE}/projects/{project['id']}/summary").get_json()
    assert summary["rollup"] == {
        "budget": "135.00",
        "actuals": "80.00",
        "variance": "55.00",
        "over_budget": False,
    }
    report = signed_client.get(f"{BASE}/reports").get_json()
    assert report["projects"][0]["rollup"] == summary["rollup"]
    assert report["activities"][0]["rollup"]["budget"] == "30.00"
    assert report["budget"] == "135.00"
    assert all(log["created_at"].endswith("+00:00") for log in report["timeline"])


def test_migration_compiles_for_postgresql():
    from io import StringIO

    migration = importlib.import_module(
        "migrations.versions.e2a9417c630b_add_project_tracking"
    )
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert sql.count("CREATE TABLE ") == 6
    assert "FOREIGN KEY(activity_id) REFERENCES project_activities (id)" in sql
    assert "CHECK (activity_id IS NULL OR project_id IS NULL)" in sql
    assert "share_project_id_user_id UNIQUE (project_id, user_id)" in sql
