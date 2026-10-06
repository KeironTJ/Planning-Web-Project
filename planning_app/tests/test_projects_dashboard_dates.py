"""Dashboard attention previews and UK presentation without changing wire dates."""

from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from werkzeug.exceptions import BadRequest

from app.extensions import db
from app.projects.dates import form_date, uk_date
from app.projects.models import Task
from app.projects import services as svc
from tests.test_projects import BASE, create, grant, patch, sign_in
from tests.test_projects_navigation import edit_data


@pytest.fixture
def signed_client(client, planner_user):
    sign_in(client, planner_user)
    return client


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ""), ("", ""), ("2026-10-05", "05/10/2026"),
     (date(2024, 2, 29), "29/02/2024"), ("0001-01-01", "01/01/0001")],
)
def test_calendar_date_format(value, expected):
    assert uk_date(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", ""), ("29/02/2024", "2024-02-29"),
     ("05/10/2026", "2026-10-05"), ("2026-10-05", "2026-10-05"),
     ("01/01/0001", "0001-01-01")],
)
def test_uk_form_dates_and_legacy_iso(value, expected):
    assert form_date(value, "deadline") == expected


@pytest.mark.parametrize("value", ["29/02/2023", "31/04/2026", "05/13/2026", "5/10/2026", "bad", "2026-02-31"])
def test_invalid_uk_form_dates_are_explicit(value):
    with pytest.raises(BadRequest, match="dd/mm/yyyy"):
        form_date(value, "deadline")


def test_shared_timestamp_formatter_uk_and_existing_formats(app):
    formatter = app.jinja_env.filters["utcfmt"]
    value = datetime(2026, 10, 5, 9, 7, 3, tzinfo=timezone.utc)
    result = str(formatter(value.isoformat(), "uk-datetime"))
    assert ">05/10/2026 09:07:03</time>" in result
    assert 'data-utc="2026-10-05T09:07:03+00:00"' in result
    assert 'data-fmt="uk-datetime"' in result
    assert ">05 Oct 2026 09:07</time>" in str(formatter(value, "datetime-year"))
    assert ">05 Oct 2026</time>" in str(formatter(value, "date-only"))


@pytest.mark.parametrize("kind", ["projects", "activities", "tasks"])
def test_full_forms_save_uk_dates_and_retain_invalid_draft(signed_client, planner_user, kind):
    data = {
        "name": "UK form work", "department": "Planning", "owner_id": planner_user.id,
        "priority": "normal", "budget": "0", "actuals": "0",
        "start_date": "01/10/2026", "end_date": "09/10/2026", "deadline": "10/10/2026",
    }
    response = signed_client.post(f"/projects/{kind}/new", data=data)
    assert response.status_code == 302
    item = signed_client.get(f"{BASE}/{kind}").get_json()["items"][0]
    assert item["start_date"] == "2026-10-01" and item["deadline"] == "2026-10-10"
    html = signed_client.get(f"/projects/{kind}/{item['id']}/edit").get_data(as_text=True)
    assert 'value="10/10/2026"' in html and 'value="01/10/2026"' in html
    assert "data-uk-date" in html
    data["deadline"] = "31/02/2026"
    response = signed_client.post(f"/projects/{kind}/new", data=data)
    assert response.status_code == 400
    assert b'dd/mm/yyyy' in response.data and b'value="31/02/2026"' in response.data
    assert signed_client.get(f"{BASE}/{kind}").get_json()["total"] == 1
    response = signed_client.post(
        f"/projects/{kind}/{item['id']}/edit",
        data=edit_data(item, planner_user, deadline="", start_date="01/10/2026", end_date="09/10/2026"),
    )
    assert response.status_code == 302
    assert signed_client.get(f"{BASE}/{kind}/{item['id']}").get_json()["deadline"] is None


def test_api_keeps_iso_and_rejects_uk_wire_dates(signed_client):
    task = create(signed_client, deadline="2026-10-05")
    assert patch(signed_client, task, deadline="05/10/2026").status_code == 400
    assert signed_client.get(f"{BASE}/tasks/{task['id']}").get_json()["deadline"] == "2026-10-05"


def test_dates_across_work_surfaces_and_archived_history(signed_client):
    task = create(
        signed_client, name="UK display", start_date="2026-10-01",
        end_date="2026-10-04", deadline="2026-10-05",
    )
    for path in (
        "/projects/tasks?view=list", "/projects/tasks?view=hierarchy",
        "/projects/tasks?view=board", "/projects/tasks?view=timeline",
        "/projects/timeline", f"/projects/tasks/{task['id']}",
    ):
        html = signed_client.get(path).get_data(as_text=True)
        assert "05/10/2026" in html
    for path in ("/projects/", "/projects/logs", "/projects/reports", f"/projects/tasks/{task['id']}"):
        html = signed_client.get(path).get_data(as_text=True)
        assert 'data-fmt="uk-datetime"' in html
        assert re.search(r">\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}</time>", html)
    assert signed_client.delete(f"{BASE}/tasks/{task['id']}", json={"version": task["version"]}).status_code == 200
    for path in ("/projects/archive", f"/projects/tasks/{task['id']}/history"):
        html = signed_client.get(path).get_data(as_text=True)
        assert 'data-fmt="uk-datetime"' in html


def test_attention_counts_all_kinds_preview_order_and_closed_exclusion(signed_client, planner_user):
    today = date.today()
    all_overdue = []
    for kind in svc.MODELS:
        for offset in range(3):
            all_overdue.append(create(
                signed_client, kind, name=f"{kind} overdue {offset}",
                deadline=(today - timedelta(days=offset + 1)).isoformat(),
            ))
        create(signed_client, kind, deadline=today.isoformat())
        create(signed_client, kind, deadline=(today + timedelta(days=14)).isoformat())
        create(signed_client, kind, deadline=(today + timedelta(days=15)).isoformat())
        closed = create(signed_client, kind, deadline=(today - timedelta(days=5)).isoformat())
        assert patch(signed_client, closed, status="cancelled").status_code == 200
        active = patch(signed_client, all_overdue[-1], status="active").get_json()
        assert patch(signed_client, active, status="blocked").status_code == 200
    summary = svc.dashboard_attention(svc.visible_items(planner_user), planner_user)
    assert summary["overdue"]["total"] == 9
    assert summary["overdue"]["counts"] == dict.fromkeys(svc.MODELS, 3)
    assert len(summary["overdue"]["items"]) == 5
    dates = [item["deadline"] for item in summary["overdue"]["items"]]
    assert dates == sorted(dates)
    assert summary["blocked"]["total"] == 3
    assert summary["upcoming"]["total"] == 6
    assert all(item["status_actions"] for section in summary.values() for item in section["items"])
    html = signed_client.get("/projects/").get_data(as_text=True)
    assert "Overdue (9)" in html and "Blocked (3)" in html and "Due soon (6)" in html
    assert html.index("Needs attention") < html.index("Recent logs")
    assert html.count('data-attention-item=') == 13
    assert '<section class="mb-4" aria-labelledby="dashboard-progress-title">' in html
    assert '<section class="mb-4" aria-labelledby="dashboard-hierarchy-title">' in html
    assert 'data-state-key="dashboard-progress"' not in html
    assert 'data-state-key="dashboard-hierarchy"' not in html


class LinkParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs).get("href"))


def test_dashboard_links_match_counts_and_status_actions_return(signed_client):
    task = create(signed_client, deadline=(date.today() - timedelta(days=1)).isoformat())
    html = signed_client.get("/projects/").get_data(as_text=True)
    links = LinkParser(html).links
    for group in ("overdue", "blocked", "upcoming"):
        for kind in svc.MODELS:
            matching = any(
                urlsplit(link).path == f"/projects/{kind}"
                and parse_qs(urlsplit(link).query) == {"quick": [group], "view": ["list"]}
                for link in links
            )
            assert matching == (group == "overdue" and kind == "tasks")
    response = signed_client.post(
        f"/projects/tasks/{task['id']}/status",
        data={"version": task["version"], "status": "active", "return_to": "/projects/"},
    )
    assert response.status_code == 302 and response.location == "/projects/"
    assert db.session.get(Task, task["id"]).status.value == "active"


def test_viewers_have_no_attention_actions_and_private_work_is_hidden(
    signed_client, client, viewer_user
):
    project = create(signed_client, "projects", deadline=(date.today() - timedelta(days=1)).isoformat())
    activity = create(signed_client, "activities", project_id=project["id"], deadline=project["deadline"])
    create(signed_client, activity_id=activity["id"], deadline=project["deadline"])
    create(signed_client, name="Private attention", deadline=project["deadline"])
    grant(signed_client, project, user_id=viewer_user.id, role="viewer")
    sign_in(client, viewer_user)
    html = client.get("/projects/").get_data(as_text=True)
    assert "Overdue (3)" in html and "Private attention" not in html
    assert html.count('data-attention-item=') == 3
    assert 'data-quick-edit ' not in html
    assert 'aria-label="Status actions' not in html
    assert 'name="return_to" value="/projects/"' not in html


def test_empty_attention_is_explicit(signed_client):
    html = signed_client.get("/projects/").get_data(as_text=True)
    for label in ("overdue", "blocked", "due soon"):
        assert f"No {label} work." in html


@pytest.mark.parametrize("groups", [1, 2, 3])
def test_attention_grid_only_contains_nonempty_panels(signed_client, groups):
    overdue = create(
        signed_client, deadline=(date.today() - timedelta(days=1)).isoformat()
    )
    if groups >= 2:
        active = patch(signed_client, overdue, status="active").get_json()
        patch(signed_client, active, status="blocked")
    if groups == 3:
        create(signed_client, deadline=date.today().isoformat())
    response = signed_client.get("/projects/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    section = html.split('aria-labelledby="dashboard-attention-title">')[1].split(
        'aria-label="Clear attention groups"'
    )[0]
    assert 'class="work-attention-grid mb-4"' in section
    assert section.count('class="card h-100') == groups
    assert "col-xl-4" not in section


def test_archived_work_disappears_from_attention(signed_client):
    task = create(signed_client, deadline=(date.today() - timedelta(days=1)).isoformat())
    assert b"Overdue (1)" in signed_client.get("/projects/").data
    assert signed_client.delete(f"{BASE}/tasks/{task['id']}", json={"version": task["version"]}).status_code == 200
    assert b"No overdue work." in signed_client.get("/projects/").data


def test_dashboard_visible_order_compact_progress_and_unique_shortcuts(signed_client):
    project = create(signed_client, "projects", name="Visible dashboard project")
    create(signed_client, "activities", project_id=project["id"])
    completed = create(signed_client, name="Finished dashboard task")
    active = patch(signed_client, completed, status="active").get_json()
    patch(signed_client, active, status="completed")
    cancelled = create(signed_client, name="Cancelled dashboard task")
    patch(signed_client, cancelled, status="cancelled")
    create(signed_client, name="Open dashboard task")
    html = signed_client.get("/projects/").get_data(as_text=True)
    assert (
        html.index("Progress overview") < html.index("Needs attention")
        < html.index("All visible work at a glance") < html.index("Recent logs")
    )
    assert html.count('class="work-dashboard-progress h-100"') == 3
    assert 'aria-label="Tasks completed" aria-valuenow="50.0"' in html
    assert "3 total" in html
    assert "1 outstanding / 1 cancelled" in html
    assert "Visible dashboard project" in html
    assert "data-attention-item" not in html
    assert "lists\"" not in html.split('id="dashboard-attention-title"')[1].split(
        'id="dashboard-hierarchy-title"'
    )[0]
    assert "Create and find work" not in html
    assert "My work shortcuts" not in html
    assert "Workflow status guide" not in html
    links = LinkParser(html).links
    assert links.count("/projects/timeline") == 1
    for kind in svc.MODELS:
        assert sum(
            urlsplit(link).path == f"/projects/{kind}"
            and parse_qs(urlsplit(link).query) == {"quick": ["mine"], "view": ["list"]}
            for link in links
        ) == 1


def test_home_work_dates_are_uk(signed_client):
    deadline = date.today() - timedelta(days=1)
    create(signed_client, deadline=deadline.isoformat())
    html = signed_client.get("/").get_data(as_text=True)
    assert uk_date(deadline) in html
    assert deadline.isoformat() not in html
