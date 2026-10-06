"""Shared permission-safe search and quick filters for every work view."""

from datetime import date, timedelta
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_projects import BASE, create, grant, patch, sign_in
from tests.test_projects_views import ViewParser


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


def results(client, kind="tasks", **filters):
    response = client.get(f"{BASE}/{kind}", query_string=filters)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_search_name_description_case_whitespace_and_literal_wildcards(signed_client, kind):
    name = create(signed_client, kind, name="Special 50%_Work")
    description = create(signed_client, kind, description="Needle in the notes")
    create(signed_client, kind, name="Special 500XWork")
    assert [row["id"] for row in results(signed_client, kind, q="  50%_wORK  ")["items"]] == [name["id"]]
    assert [row["id"] for row in results(signed_client, kind, q="NEEDLE")["items"]] == [description["id"]]
    assert results(signed_client, kind, q="   ")["total"] == 3
    assert results(signed_client, kind, q="no match")["total"] == 0
    assert results(signed_client, kind, q="9" * 200)["total"] == 0


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_work_lists_sort_by_earliest_deadline_then_name(signed_client, kind):
    later = create(signed_client, kind, name="Later due", deadline="2030-02-01")
    undated = create(signed_client, kind, name="No due date")
    earlier = create(signed_client, kind, name="Earlier due", deadline="2030-01-01")

    items = results(signed_client, kind, sort="deadline")["items"]
    assert [item["id"] for item in items[:3]] == [
        earlier["id"],
        later["id"],
        undated["id"],
    ]


def test_full_reference_search_covers_all_locations_and_move(signed_client):
    project = create(signed_client, "projects")
    activity = create(signed_client, "activities", project_id=project["id"])
    independent = create(signed_client, "activities")
    nested = create(signed_client, activity_id=activity["id"])
    direct = create(signed_client, project_id=project["id"])
    independent_task = create(signed_client, activity_id=independent["id"])
    standalone = create(signed_client)
    for item in [project, activity, independent, nested, direct, independent_task, standalone]:
        found = results(signed_client, item["kind"], q=item["reference"].lower())
        assert [row["id"] for row in found["items"]] == [item["id"]]
    wrong_location = f'{project["id"] + 1:02}.{activity["id"]:02}.{nested["id"]:02}'
    assert results(signed_client, q=wrong_location)["total"] == 0
    moved = patch(signed_client, direct, project_id=None).get_json()
    assert results(signed_client, q=direct["reference"])["total"] == 0
    assert results(signed_client, q=moved["reference"])["items"][0]["id"] == direct["id"]


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_quick_filters_open_blocked_due_dates_and_composition(signed_client, kind):
    today = date.today()
    overdue = create(signed_client, kind, name="Matching overdue", deadline=(today - timedelta(days=1)).isoformat())
    upcoming = create(signed_client, kind, deadline=(today + timedelta(days=14)).isoformat())
    due_today = create(signed_client, kind, deadline=today.isoformat())
    create(signed_client, kind, deadline=(today + timedelta(days=15)).isoformat())
    blocked = create(signed_client, kind)
    active = patch(signed_client, blocked, status="active").get_json()
    assert patch(signed_client, active, status="blocked").status_code == 200
    closed = create(signed_client, kind, deadline=(today - timedelta(days=1)).isoformat())
    assert patch(signed_client, closed, status="cancelled").status_code == 200
    assert results(signed_client, kind, quick="open")["total"] == 5
    assert [row["id"] for row in results(signed_client, kind, quick="blocked")["items"]] == [blocked["id"]]
    assert [row["id"] for row in results(signed_client, kind, quick="overdue")["items"]] == [overdue["id"]]
    assert {row["id"] for row in results(signed_client, kind, quick="upcoming")["items"]} == {upcoming["id"], due_today["id"]}
    assert results(signed_client, kind, q="matching", quick="overdue", priority="normal")["total"] == 1
    assert results(signed_client, kind, quick="overdue", deadline="upcoming")["total"] == 0
    assert results(signed_client, kind, quick="open", status="cancelled")["total"] == 0


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_owned_by_me_is_not_shared_or_assigned_work(signed_client, client, viewer_user, kind):
    owned = create(signed_client, kind)
    shared = create(signed_client, kind, owner_id=viewer_user.id)
    assert results(signed_client, kind, quick="mine")["items"][0]["id"] == owned["id"]
    grant(signed_client, owned, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    assert [row["id"] for row in results(client, kind, quick="mine")["items"]] == [shared["id"]]


def test_assigned_filter_keeps_access_boundary_and_includes_closed_tasks(
    signed_client, client, planner_user, viewer_user
):
    assigned = create(signed_client, assigned_user_ids=[planner_user.id])
    create(signed_client)
    assert patch(signed_client, assigned, status="cancelled").status_code == 200
    assert results(signed_client, quick="assigned")["total"] == 1
    assert results(signed_client, quick="assigned", status="planned")["total"] == 0
    grant(signed_client, assigned, user_id=viewer_user.id, role="viewer")
    assigned = signed_client.get(f"{BASE}/tasks/{assigned['id']}").get_json()
    assert patch(signed_client, assigned, assigned_user_ids=[viewer_user.id]).status_code == 200
    sign_in(client, viewer_user)
    assert results(client, quick="assigned")["total"] == 1
    sign_in(signed_client, planner_user)
    share = grant(signed_client, assigned, user_id=viewer_user.id, role="viewer")[0]
    assert signed_client.delete(f"{BASE}/tasks/{assigned['id']}/shares/{share['id']}").status_code == 204
    sign_in(client, viewer_user)
    assert results(client, quick="assigned")["total"] == 0


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_search_and_quick_filters_never_expose_private_or_deleted_work(
    signed_client, client, viewer_user, kind
):
    private = create(signed_client, kind, name="Private needle")
    deleted = create(signed_client, kind, name="Deleted needle")
    assert signed_client.delete(f"{BASE}/{kind}/{deleted['id']}", json={"version": deleted["version"]}).status_code == 200
    assert results(signed_client, kind, q="Deleted needle")["total"] == 0
    sign_in(client, viewer_user)
    for search in ["needle", private["reference"]]:
        for quick in ["", "open", "mine", "blocked", "overdue", "upcoming"]:
            assert results(client, kind, q=search, quick=quick)["total"] == 0


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_invalid_search_and_quick_filters_are_explicit_errors(signed_client, kind):
    for filters in [{"q": "x" * 201}, {"quick": "unknown"}, {"sort": "unknown"}]:
        for prefix in ["/projects", BASE]:
            response = signed_client.get(f"{prefix}/{kind}", query_string=filters)
            assert response.status_code == 400
    if kind != "tasks":
        assert signed_client.get(f"{BASE}/{kind}?quick=assigned").status_code == 400


@pytest.mark.parametrize("view", ["list", "hierarchy", "board", "timeline"])
def test_search_quick_links_toggle_and_preserve_context(signed_client, view):
    project = create(signed_client, "projects")
    for number in range(3):
        create(signed_client, name=f"Search target {number}", project_id=project["id"])
    response = signed_client.get("/projects/tasks", query_string={
        "q": "Search target", "quick": "open", "view": view, "priority": "normal",
        "project_id": project["id"], "per_page": 1, "page": 2,
    })
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    parsed = ViewParser(html)
    assert "3</strong> matching tasks" in html
    assert parsed.inputs["q"] == "Search target"
    assert parsed.inputs["quick"] == "open"
    assert parsed.inputs["project_id"] == str(project["id"])
    assert "page" not in parsed.inputs
    assert 'id="priority"' in html
    assert '<details data-work-more-filters open>' in html
    assert "More filters (2 active)" in html
    links = [link for link in parsed.links if "data-work-quick-filter" in link]
    assert len(links) == 6
    for link in links:
        query = parse_qs(urlsplit(link["href"]).query, keep_blank_values=True)
        assert "page" not in query
        assert query["q"] == ["Search target"]
        assert query["view"] == [view]
        assert query["priority"] == ["normal"]
        assert query["project_id"] == [str(project["id"])]
        assert query["per_page"] == ["1"]
        if link["data-work-quick-filter"] == "open":
            assert link["aria-current"] == "true" and query["quick"] == [""]
    assert all(parse_qs(urlsplit(link["href"]).query)["q"] == ["Search target"] for link in parsed.views.values())
    assert "Page 2 of 3 / 3 items" in html


def test_search_is_escaped_and_parent_context_does_not_change_task_matches(signed_client):
    project = create(signed_client, "projects", name="Parent context only")
    create(signed_client, name="<script>needle</script>", project_id=project["id"])
    create(signed_client, name="Unmatched sibling", project_id=project["id"])
    response = signed_client.get("/projects/tasks", query_string={"q": "<script>needle</script>"})
    assert response.status_code == 200
    assert b"<script>needle</script>" not in response.data
    assert b"&lt;script&gt;needle&lt;/script&gt;" in response.data
    assert b"Parent context only" in response.data
    assert b"Unmatched sibling" not in response.data
    assert results(signed_client, q="Parent context only")["total"] == 0


def test_ordinary_filters_are_collapsed_until_applied(signed_client):
    for query in [{}, {"q": "needle", "quick": "mine"}]:
        html = signed_client.get("/projects/tasks", query_string=query).get_data(as_text=True)
        details = re.search(r"<details data-work-more-filters[^>]*>", html)
        assert details is not None and "open" not in details.group()
    html = signed_client.get("/projects/tasks?status=planned").get_data(as_text=True)
    assert '<details data-work-more-filters open>' in html
    assert "More filters (1 active)" in html
