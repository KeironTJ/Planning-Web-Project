"""Exact model decisions, one-piece review, permissions, and persistence."""

import json
import runpy
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.auth.models import AuditLog, Permission, Role, User
from app.core.exceptions import NotFoundError, ValidationError
from app.forecasting.models import ModelClassification
from app.forecasting.services import (
    classify_model,
    get_model_review,
    remove_model_classification,
    save_model_classification,
)
from app.operations.models import WorksOrder

from .conftest import login


PAGE = "/ai-tools/models"


@pytest.mark.parametrize("model", [None, "", " \t "])
def test_blank_models_are_always_excluded(model):
    assert classify_model(model, {}) == "excluded"


def test_matching_is_exact_and_never_inferred_from_a_prefix():
    mapping = {"ACO": "cover_only", "Sofa": "full_unit", "Scatter": "scatter"}
    assert classify_model(" ACO ", mapping) == "cover_only"
    assert classify_model("ACO Sofa", mapping) == "unclassified"
    assert classify_model("aco", mapping) == "unclassified"
    assert classify_model("LCO", mapping) == "unclassified"
    assert classify_model("Scatter", {}) == "unclassified"
    assert classify_model("Sofa", mapping) == "full_unit"


def test_save_update_and_remove_are_audited(db_session, admin_user):
    item = save_model_classification(" ACO ", "cover_only", admin_user.id)
    assert item.model == "ACO"
    assert item.updated_by_id == admin_user.id
    assert item.updated_at is not None

    save_model_classification("ACO", "excluded", admin_user.id)
    assert ModelClassification.query.count() == 1
    audits = AuditLog.query.filter_by(
        action="forecast_model_classification_saved"
    ).order_by(AuditLog.id).all()
    assert json.loads(audits[-1].details) == {
        "model": "ACO", "previous": "cover_only", "category": "excluded"
    }

    remove_model_classification("ACO", admin_user.id)
    assert ModelClassification.query.count() == 0
    assert AuditLog.query.filter_by(
        action="forecast_model_classification_removed"
    ).count() == 1
    with pytest.raises(NotFoundError):
        remove_model_classification("ACO", admin_user.id)


@pytest.mark.parametrize(
    ("model", "category"),
    [("", "full_unit"), ("   ", "excluded"), ("X" * 256, "full_unit"),
     ("Sofa", ""), ("Sofa", "unclassified"), ("Sofa", "invalid")],
)
def test_invalid_decisions_do_not_write(db_session, admin_user, model, category):
    with pytest.raises(ValidationError):
        save_model_classification(model, category, admin_user.id)
    assert ModelClassification.query.count() == 0
    assert AuditLog.query.filter_by(
        action="forecast_model_classification_saved"
    ).count() == 0


def test_review_counts_records_and_reports_quantity_exceptions(db_session, admin_user):
    save_model_classification("Sofa", "full_unit", admin_user.id)
    save_model_classification("ACO", "cover_only", admin_user.id)
    save_model_classification("Archived", "excluded", admin_user.id)
    db_session.add_all([
        WorksOrder(job_num="J1", assembly_seq=0, model="Sofa", required_qty=1),
        WorksOrder(job_num="J2", assembly_seq=0, model=" Sofa ", required_qty=3),
        WorksOrder(job_num="J3", assembly_seq=0, model="ACO", required_qty=1),
        WorksOrder(job_num="J4", assembly_seq=0, model="LCO", required_qty=None),
        WorksOrder(job_num="J5", assembly_seq=0, model="", required_qty=1),
        WorksOrder(job_num="J6", assembly_seq=0, model=None, required_qty=1),
    ])
    db_session.commit()
    review = {item.model: item for item in get_model_review()}
    assert review["Sofa"].record_count == 2
    assert review["Sofa"].required_qty_total == Decimal("4")
    assert review["Sofa"].non_single_qty_count == 1
    assert review["ACO"].category == "cover_only"
    assert review["LCO"].category == "unclassified"
    assert review["LCO"].missing_qty_count == 1
    assert review[""].category == "excluded"
    assert review[""].record_count == 2
    assert review["Archived"].record_count == 0
    assert sum(item.record_count for item in review.values()) == 6


def test_classifications_survive_a_works_order_refresh(db_session, admin_user):
    save_model_classification("ACO", "cover_only", admin_user.id)
    db_session.add(WorksOrder(job_num="J1", model="ACO", required_qty=1))
    db_session.commit()
    WorksOrder.query.delete()
    db_session.add(WorksOrder(job_num="J2", model="ACO", required_qty=1))
    db_session.commit()
    review = get_model_review()
    assert review[0].category == "cover_only"
    assert review[0].record_count == 1


def test_anonymous_requests_require_login(client):
    assert client.get("/ai-tools/").status_code == 302
    assert client.get(PAGE).status_code == 302
    assert client.post(PAGE + "/save").status_code == 302
    assert client.post(PAGE + "/remove").status_code == 302


def test_viewer_can_read_but_cannot_change_decisions(client, viewer_user):
    login(client, viewer_user.email, "Viewer!Pass1234")
    response = client.get(PAGE)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Read-only access" in html
    assert 'action="' + PAGE + '/save"' not in html
    assert client.post(PAGE + "/save", data={
        "model": "ACO", "category": "cover_only"
    }).status_code == 403
    assert client.post(PAGE + "/remove", data={"model": "ACO"}).status_code == 403


def test_write_permission_alone_does_not_grant_access(db_session, client):
    role = Role(name="forecast_write_only")
    role.permissions.append(Permission.query.filter_by(name="manage_planning").one())
    user = User(username="write_only", email="write_only@test.com", is_active=True)
    user.set_password("Test!Pass1234")
    user.roles.append(role)
    db_session.add(user)
    db_session.commit()
    login(client, user.email, "Test!Pass1234")
    assert client.get(PAGE).status_code == 403
    assert client.get("/ai-tools/").status_code == 403
    assert client.post(PAGE + "/save", data={
        "model": "ACO", "category": "cover_only"
    }).status_code == 403


def test_planner_can_save_review_and_reset(client, planner_user, db_session):
    db_session.add(WorksOrder(job_num="J1", model="ACO", required_qty=2))
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(PAGE + "/save", data={
        "model": "ACO", "category": "cover_only"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "Model classification saved." in response.get_data(as_text=True)
    assert "quantity other than one" in response.get_data(as_text=True)
    assert ModelClassification.query.one().category == "cover_only"
    response = client.post(PAGE + "/remove", data={
        "model": "ACO"
    }, follow_redirects=True)
    assert "The model now needs review." in response.get_data(as_text=True)
    assert ModelClassification.query.count() == 0


def test_invalid_post_shows_error_not_success(client, planner_user):
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(PAGE + "/save", data={
        "model": "ACO", "category": "invalid"
    }, follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "Choose full unit, cover-only, Scatter, or excluded." in html
    assert "Model classification saved." not in html
    assert ModelClassification.query.count() == 0


def test_empty_state_and_model_names_are_escaped(client, planner_user, db_session):
    login(client, planner_user.email, "Planner!Pass1234")
    assert "No COOIS models" in client.get(PAGE).get_data(as_text=True)
    db_session.add(WorksOrder(job_num="J1", model="<script>alert(1)</script>"))
    db_session.commit()
    html = client.get(PAGE).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_csrf_protects_classification_writes(app, client, planner_user):
    login(client, planner_user.email, "Planner!Pass1234")
    original = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert client.post(PAGE + "/save", data={
            "model": "ACO", "category": "cover_only"
        }).status_code == 400
        assert client.post(PAGE + "/remove", data={"model": "ACO"}).status_code == 400
        assert ModelClassification.query.count() == 0
    finally:
        app.config["WTF_CSRF_ENABLED"] = original


def test_view_only_planning_role_has_a_navigation_link(db_session, client):
    role = Role(name="forecast_read_only")
    role.permissions.append(Permission.query.filter_by(name="view_planning").one())
    user = User(username="read_only", email="read_only@test.com", is_active=True)
    user.set_password("Test!Pass1234")
    user.roles.append(role)
    db_session.add(user)
    db_session.commit()
    login(client, user.email, "Test!Pass1234")
    html = client.get(PAGE).get_data(as_text=True)
    assert 'href="' + PAGE + '"' in html
    assert "Read-only access" in html


def test_ai_tools_landing_and_home_link(client, viewer_user):
    """AI Tools is a separate destination, not a tool card within Planning."""
    from .test_section_landings import LandingLinksParser

    login(client, viewer_user.email, "Viewer!Pass1234")
    response = client.get("/ai-tools/")
    assert response.status_code == 200
    assert "<h4 class=\"mb-0 fw-semibold\">AI Tools</h4>" in response.get_data(as_text=True)
    parser = LandingLinksParser()
    parser.feed(response.get_data(as_text=True))
    assert PAGE in parser.main_links
    assert PAGE in parser.sidebar_links
    parser = LandingLinksParser()
    parser.feed(client.get("/planning/dashboard").get_data(as_text=True))
    assert PAGE not in parser.main_links
    parser = LandingLinksParser()
    parser.feed(client.get("/").get_data(as_text=True))
    assert "/ai-tools/dashboard" in parser.main_links


def test_ai_tools_hidden_without_read_permission(client, purchasing_user):
    login(client, purchasing_user.email, "Purchasing!Pass1234")
    assert client.get("/ai-tools/").status_code == 403
    assert client.get(PAGE).status_code == 403
    html = client.get("/").get_data(as_text=True)
    assert 'id="aiToolsMenu"' not in html
    assert 'href="/ai-tools/dashboard"' not in html


def test_ai_overview_explains_goals_and_distinguishes_available_tools(client, viewer_user):
    """Keep the overview aligned with the agreed targets and delivery status."""
    login(client, viewer_user.email, "Viewer!Pass1234")
    response = client.get("/ai-tools/dashboard")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for text in (
        "Purpose and goal",
        "Monthly net sales",
        "next three months",
        "next twelve",
        "Net invoiced sales in GBP",
        "Full units and cover-only work",
        "order receipt to actual shipment",
        "From data to a useful forecast",
        "Available tools",
        "Model classification is the first step",
    ):
        assert text in html
    assert 'href="' + PAGE + '"' in html


def test_migration_upgrade_constraints_and_downgrade():
    """Exercise the new revision on an isolated database, not a live instance."""
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations" / "versions"
        / "a7c31d9e2b60_add_forecast_model_classifications.py"
    )
    migration = runpy.run_path(str(path))
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration["upgrade"]()
            table = sa.Table(
                "forecast_model_classifications",
                sa.MetaData(),
                autoload_with=connection,
            )
            connection.execute(table.insert().values(
                model="ACO", category="cover_only", updated_at=sa.func.current_timestamp()
            ))
            with connection.begin_nested():
                with pytest.raises(sa.exc.IntegrityError):
                    connection.execute(table.insert().values(
                        model="ACO", category="full_unit",
                        updated_at=sa.func.current_timestamp()
                    ))
            with connection.begin_nested():
                with pytest.raises(sa.exc.IntegrityError):
                    connection.execute(table.insert().values(
                        model="LCO", category="invalid",
                        updated_at=sa.func.current_timestamp()
                    ))
            with Operations.context(context):
                migration["downgrade"]()
            assert "forecast_model_classifications" not in sa.inspect(
                connection
            ).get_table_names()
            assert "users" in sa.inspect(connection).get_table_names()
    finally:
        engine.dispose()
