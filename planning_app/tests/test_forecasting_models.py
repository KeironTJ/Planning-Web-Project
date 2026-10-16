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
from app.forecasting.models import ModelCategory, ModelClassification
from app.forecasting.services import (
    get_model_categories,
    get_model_review,
    remove_model_category,
    remove_model_classification,
    save_model_category,
    save_model_classification,
    save_model_classifications,
    save_model_classification_updates,
)
from app.operations.models import WorksOrder

from .conftest import login


PAGE = "/ai-tools/models"


def make_category(user_id, name):
    return save_model_category(name, "", user_id)


def test_categories_are_customizable_and_keep_assignments_on_rename(
    db_session, admin_user
):
    category = save_model_category(
        "  Covers & Scatters  ", "  Products with alternate demand treatment  ",
        admin_user.id,
    )
    item = save_model_classification("ACO", category.id, admin_user.id)
    updated = save_model_category(
        "Upholstery only", "Cover-only models", admin_user.id, category.id
    )
    assert updated.id == category.id
    assert item.category.name == "Upholstery only"
    assert item.category.description == "Cover-only models"
    assert [item.name for item in get_model_categories()] == ["Upholstery only"]
    audits = AuditLog.query.filter_by(
        action="forecast_model_category_saved"
    ).order_by(AuditLog.id).all()
    assert json.loads(audits[0].details)["previous"] is None
    assert json.loads(audits[1].details)["previous"]["name"] == "Covers & Scatters"


@pytest.mark.parametrize(
    ("name", "description", "message"),
    [
        (" ", "", "name is required"),
        ("X" * 81, "", "80 characters"),
        ("Valid", "D" * 256, "255 characters"),
    ],
)
def test_invalid_category_values_are_rejected(db_session, admin_user, name, description, message):
    with pytest.raises(ValidationError, match=message):
        save_model_category(name, description, admin_user.id)
    assert ModelCategory.query.count() == 0


def test_category_names_are_unique_without_case_sensitivity(db_session, admin_user):
    make_category(admin_user.id, "Cover-only")
    with pytest.raises(ValidationError, match="already exists"):
        save_model_category(" COVER-ONLY ", "", admin_user.id)
    assert ModelCategory.query.count() == 1


def test_category_cannot_be_deleted_while_models_are_assigned(db_session, admin_user):
    category = make_category(admin_user.id, "Accessories")
    save_model_classification("ACO", category.id, admin_user.id)
    with pytest.raises(ValidationError, match="Move the models"):
        remove_model_category(category.id, admin_user.id)
    assert ModelCategory.query.count() == 1

    remove_model_classification("ACO", admin_user.id)
    remove_model_category(category.id, admin_user.id)
    assert ModelCategory.query.count() == 0
    assert AuditLog.query.filter_by(
        action="forecast_model_category_removed"
    ).count() == 1


def test_save_update_and_remove_are_audited(db_session, admin_user):
    category = make_category(admin_user.id, "Covers & Scatters")
    another = make_category(admin_user.id, "Finished units")
    item = save_model_classification(" ACO ", category.id, admin_user.id)
    assert item.model == "ACO"
    assert item.updated_by_id == admin_user.id
    assert item.updated_at is not None

    save_model_classification("ACO", another.id, admin_user.id)
    assert ModelClassification.query.count() == 1
    audits = AuditLog.query.filter_by(
        action="forecast_model_classification_saved"
    ).order_by(AuditLog.id).all()
    details = json.loads(audits[-1].details)
    assert details["model"] == "ACO"
    assert details["previous"] == {
        "category_id": category.id, "category_name": category.name
    }
    assert details["category_id"] == another.id
    assert details["category_name"] == another.name

    remove_model_classification("ACO", admin_user.id)
    assert ModelClassification.query.count() == 0
    assert AuditLog.query.filter_by(
        action="forecast_model_classification_removed"
    ).count() == 1
    with pytest.raises(NotFoundError):
        remove_model_classification("ACO", admin_user.id)


def test_bulk_classification_updates_new_and_existing_models_once(db_session, admin_user):
    category = make_category(admin_user.id, "Upholstery")
    another = make_category(admin_user.id, "Other products")
    save_model_classification("ACO", another.id, admin_user.id)
    updated = save_model_classifications(
        ["ACO", "LCO", " LCO "], category.id, admin_user.id
    )
    assert updated == 2
    items = {item.model: item for item in ModelClassification.query.all()}
    assert set(items) == {"ACO", "LCO"}
    assert all(item.category_id == category.id for item in items.values())
    audit = AuditLog.query.filter_by(
        action="forecast_model_classifications_bulk_saved"
    ).one()
    details = json.loads(audit.details)
    assert details["models"] == ["ACO", "LCO"]
    assert details["previous"] == {
        "ACO": {"category_id": another.id, "category_name": another.name},
        "LCO": None,
    }
    assert details["category_id"] == category.id
    assert details["category_name"] == category.name


def test_save_all_model_category_changes_updates_mixed_values_in_one_audit(
    db_session, admin_user
):
    current = make_category(admin_user.id, "Current")
    next_category = make_category(admin_user.id, "Next")
    save_model_classification("ACO", current.id, admin_user.id)
    save_model_classification("LCO", current.id, admin_user.id)

    updated = save_model_classification_updates(
        [("ACO", next_category.id), ("LCO", None), ("New model", next_category.id)],
        admin_user.id,
    )

    assert updated == 3
    assert ModelClassification.query.filter_by(model="ACO").one().category_id == (
        next_category.id
    )
    assert ModelClassification.query.filter_by(model="LCO").count() == 0
    assert ModelClassification.query.filter_by(model="New model").one().category_id == (
        next_category.id
    )
    audit = AuditLog.query.filter_by(
        action="forecast_model_classifications_bulk_saved"
    ).order_by(AuditLog.id.desc()).first()
    details = json.loads(audit.details)
    assert details["models"] == ["ACO", "LCO", "New model"]
    assert details["previous"]["ACO"] == {
        "category_id": current.id, "category_name": current.name
    }
    assert details["assignments"]["LCO"] is None


def test_blank_model_group_can_be_classified_without_identifying_individual_rows(
    db_session, admin_user
):
    category = make_category(admin_user.id, "Other")
    db_session.add_all([
        WorksOrder(job_num="J1", assembly_seq=0, model=None, required_qty=1),
        WorksOrder(job_num="J2", assembly_seq=0, model="", required_qty=1),
        WorksOrder(job_num="J3", assembly_seq=0, model="   ", required_qty=2),
    ])
    db_session.commit()

    assert save_model_classification_updates([("", category.id)], admin_user.id) == 1

    review = {item.model: item for item in get_model_review()}
    assert review[""].category_id == category.id
    assert review[""].category_name == "Other"
    assert review[""].record_count == 3


def test_save_all_model_category_changes_skips_unchanged_assignments(
    db_session, admin_user
):
    category = make_category(admin_user.id, "Current")
    save_model_classification("ACO", category.id, admin_user.id)
    previous_audits = AuditLog.query.filter_by(
        action="forecast_model_classifications_bulk_saved"
    ).count()

    assert save_model_classification_updates(
        [("ACO", category.id), ("Unclassified", None)], admin_user.id
    ) == 0
    assert AuditLog.query.filter_by(
        action="forecast_model_classifications_bulk_saved"
    ).count() == previous_audits


def test_invalid_save_all_category_changes_do_not_write(
    db_session, admin_user
):
    with pytest.raises(ValidationError, match="available category"):
        save_model_classification_updates([("ACO", 999)], admin_user.id)
    with pytest.raises(ValidationError, match="only be updated once"):
        save_model_classification_updates(
            [("ACO", None), (" ACO ", None)], admin_user.id
        )
    assert ModelClassification.query.count() == 0
    assert AuditLog.query.filter_by(
        action="forecast_model_classifications_bulk_saved"
    ).count() == 0


@pytest.mark.parametrize(
    ("models", "category_id", "message"),
    [
        ([], 1, "Select at least one model"),
        (["ACO"], None, "Choose an available category"),
        (["ACO"], 999, "Choose an available category"),
        (["ACO", ""], 1, "Blank models"),
        (["X" * 256], 1, "255 characters"),
        ([f"M{i}" for i in range(501)], 1, "500 models"),
    ],
)
def test_invalid_bulk_classification_writes_nothing(
    db_session, admin_user, models, category_id, message
):
    if category_id == 1:
        category_id = make_category(admin_user.id, "Products").id
    with pytest.raises(ValidationError, match=message):
        save_model_classifications(models, category_id, admin_user.id)
    assert ModelClassification.query.count() == 0
    assert AuditLog.query.filter_by(
        action="forecast_model_classifications_bulk_saved"
    ).count() == 0


@pytest.mark.parametrize(
    ("model", "category_id"),
    [("", 1), ("   ", 1), ("X" * 256, 1), ("Sofa", None), ("Sofa", 999)],
)
def test_invalid_decisions_do_not_write(db_session, admin_user, model, category_id):
    if category_id == 1:
        category_id = make_category(admin_user.id, "Product group").id
    with pytest.raises(ValidationError):
        save_model_classification(model, category_id, admin_user.id)
    assert ModelClassification.query.count() == 0
    assert AuditLog.query.filter_by(
        action="forecast_model_classification_saved"
    ).count() == 0


def test_review_counts_records_and_reports_quantity_exceptions(db_session, admin_user):
    furniture = make_category(admin_user.id, "Furniture")
    upholstery = make_category(admin_user.id, "Upholstery")
    archived = make_category(admin_user.id, "Archived products")
    save_model_classification("Sofa", furniture.id, admin_user.id)
    save_model_classification("ACO", upholstery.id, admin_user.id)
    save_model_classification("Archived", archived.id, admin_user.id)
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
    assert review["ACO"].category_id == upholstery.id
    assert review["ACO"].category_name == "Upholstery"
    assert review["LCO"].category_id is None
    assert review["LCO"].category_name == "Unclassified"
    assert review["LCO"].missing_qty_count == 1
    assert review[""].category_id is None
    assert review[""].record_count == 2
    assert review["Archived"].record_count == 0
    assert sum(item.record_count for item in review.values()) == 6


def test_classifications_survive_a_works_order_refresh(db_session, admin_user):
    category = make_category(admin_user.id, "Soft covers")
    save_model_classification("ACO", category.id, admin_user.id)
    db_session.add(WorksOrder(job_num="J1", model="ACO", required_qty=1))
    db_session.commit()
    WorksOrder.query.delete()
    db_session.add(WorksOrder(job_num="J2", model="ACO", required_qty=1))
    db_session.commit()
    review = get_model_review()
    assert review[0].category_id == category.id
    assert review[0].category_name == "Soft covers"
    assert review[0].record_count == 1


def test_anonymous_requests_require_login(client):
    assert client.get("/ai-tools/").status_code == 302
    assert client.get(PAGE).status_code == 302
    assert client.post(PAGE + "/save").status_code == 302
    assert client.post(PAGE + "/remove").status_code == 302
    assert client.post(PAGE + "/bulk-save").status_code == 302
    assert client.post(PAGE + "/categories/save").status_code == 302
    assert client.post(PAGE + "/categories/1/remove").status_code == 302


def test_viewer_can_read_but_cannot_change_decisions(client, viewer_user):
    login(client, viewer_user.email, "Viewer!Pass1234")
    response = client.get(PAGE)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Read-only access" in html
    assert 'action="' + PAGE + '/save"' not in html
    assert client.post(PAGE + "/save", data={
        "model": "ACO", "category_id": "1"
    }).status_code == 403
    assert client.post(PAGE + "/remove", data={"model": "ACO"}).status_code == 403
    assert client.post(PAGE + "/bulk-save", data={
        "models": ["ACO"], "category_id": "1"
    }).status_code == 403
    assert client.post(PAGE + "/categories/save", data={
        "name": "Accessories"
    }).status_code == 403


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
        "model": "ACO", "category_id": "1"
    }).status_code == 403
    assert client.post(PAGE + "/bulk-save", data={
        "models": ["ACO"], "category_id": "1"
    }).status_code == 403
    assert client.post(PAGE + "/categories/save", data={
        "name": "Accessories"
    }).status_code == 403


def test_planner_can_save_review_and_reset(client, planner_user, db_session):
    category = make_category(planner_user.id, "Cover-only")
    db_session.add(WorksOrder(job_num="J1", model="ACO", required_qty=2))
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(PAGE + "/save", data={
        "model": "ACO", "category_id": str(category.id)
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "Model classification saved." in response.get_data(as_text=True)
    assert "quantity other than one" in response.get_data(as_text=True)
    assert ModelClassification.query.one().category.name == "Cover-only"
    response = client.post(PAGE + "/remove", data={
        "model": "ACO"
    }, follow_redirects=True)
    assert "The model now needs review." in response.get_data(as_text=True)
    assert ModelClassification.query.count() == 0


def test_bulk_route_classifies_selected_models_and_reports_validation(
    client, planner_user, db_session
):
    category = make_category(planner_user.id, "Cover-only")
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(PAGE + "/bulk-save", data={
        "models": ["ACO", "LCO"], "category_id": str(category.id)
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "Classification updated for 2 model(s)." in response.get_data(as_text=True)
    assert {
        item.model for item in ModelClassification.query.all()
    } == {"ACO", "LCO"}

    response = client.post(PAGE + "/bulk-save", data={
        "models": [], "category_id": str(category.id)
    }, follow_redirects=True)
    assert "Select at least one model to classify." in response.get_data(as_text=True)
    assert ModelClassification.query.count() == 2


def test_category_routes_create_rename_and_protect_assigned_categories(
    client, planner_user
):
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(PAGE + "/categories/save", data={
        "name": "Accessories", "description": "Optional components"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "Category &#39;Accessories&#39; saved." in response.get_data(as_text=True)
    category = ModelCategory.query.one()

    response = client.post(PAGE + "/save", data={
        "model": "ACO", "category_id": str(category.id)
    }, follow_redirects=True)
    assert response.status_code == 200

    response = client.post(PAGE + "/categories/save", data={
        "category_id": str(category.id),
        "name": "Cover components",
        "description": "Renamed category",
    }, follow_redirects=True)
    assert "Category &#39;Cover components&#39; saved." in response.get_data(as_text=True)
    assert ModelClassification.query.one().category.name == "Cover components"

    response = client.post(
        f"{PAGE}/categories/{category.id}/remove", follow_redirects=True
    )
    assert "Move the models in this category before deleting it." in response.get_data(as_text=True)
    assert ModelCategory.query.count() == 1

    client.post(PAGE + "/remove", data={"model": "ACO"})
    response = client.post(
        f"{PAGE}/categories/{category.id}/remove", follow_redirects=True
    )
    assert "Category removed." in response.get_data(as_text=True)
    assert ModelCategory.query.count() == 0


def test_category_route_reports_duplicates_and_invalid_ids(client, planner_user):
    login(client, planner_user.email, "Planner!Pass1234")
    client.post(PAGE + "/categories/save", data={"name": "Full unit"})
    response = client.post(PAGE + "/categories/save", data={"name": " full UNIT "},
                           follow_redirects=True)
    assert "A category with this name already exists." in response.get_data(as_text=True)
    response = client.post(PAGE + "/categories/save", data={
        "category_id": "not-a-number", "name": "Other"
    }, follow_redirects=True)
    assert "Invalid category selection." in response.get_data(as_text=True)
    assert ModelCategory.query.count() == 1


def test_invalid_post_shows_error_not_success(client, planner_user):
    login(client, planner_user.email, "Planner!Pass1234")
    response = client.post(PAGE + "/save", data={
        "model": "ACO", "category_id": "999"
    }, follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "Choose an available category." in html
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


def test_model_dropdowns_save_together(client, planner_user, db_session):
    login(client, planner_user.email, "Planner!Pass1234")
    category = make_category(planner_user.id, "A product group")
    db_session.add_all([
        WorksOrder(job_num="J1", model="ACO"),
        WorksOrder(job_num="J2", model="LCO"),
        WorksOrder(job_num="J3", model=""),
    ])
    db_session.commit()
    html = client.get(PAGE).get_data(as_text=True)
    assert 'id="classification-updates-form"' in html
    assert 'id="save-all-classifications"' in html
    assert 'id="select-all-models"' in html
    assert 'id="selected-model-category"' in html
    assert 'id="apply-selected-category"' in html
    assert 'name="models" value="ACO"' in html
    assert 'name="models" value="LCO"' in html
    assert 'class="form-check-input classification-row-select"' in html
    assert (
        'name="category_ids" class="form-select form-select-sm '
        'classification-category-select"' in html
    )
    assert 'name="models" value=""' in html
    assert 'Category for all records with a blank model name' in html
    assert "Applies to all records with a blank model name." in html
    assert f'<option value="{category.id}"' in html
    assert "A product group" in html
    assert "Unclassified</option>" in html


def test_save_all_model_dropdown_route_updates_all_submitted_rows(
    client, planner_user
):
    current = make_category(planner_user.id, "Current")
    next_category = make_category(planner_user.id, "Next")
    save_model_classification("ACO", current.id, planner_user.id)
    save_model_classification("LCO", current.id, planner_user.id)
    login(client, planner_user.email, "Planner!Pass1234")

    response = client.post(PAGE + "/save-all", data={
        "models": ["ACO", "LCO"],
        "category_ids": [str(next_category.id), ""],
    }, follow_redirects=True)

    assert "Classification updated for 2 model(s)." in response.get_data(as_text=True)
    assert ModelClassification.query.filter_by(model="ACO").one().category_id == (
        next_category.id
    )
    assert ModelClassification.query.filter_by(model="LCO").count() == 0


def test_save_all_route_can_assign_blank_model_group(client, planner_user, db_session):
    category = make_category(planner_user.id, "Other")
    db_session.add_all([
        WorksOrder(job_num="J1", model=None),
        WorksOrder(job_num="J2", model=""),
    ])
    db_session.commit()
    login(client, planner_user.email, "Planner!Pass1234")

    response = client.post(PAGE + "/save-all", data={
        "models": [""],
        "category_ids": [str(category.id)],
    }, follow_redirects=True)

    assert "Classification updated for 1 model(s)." in response.get_data(as_text=True)
    assert ModelClassification.query.filter_by(model="").one().category_id == category.id
    blank_item = next(item for item in get_model_review() if item.model == "")
    assert blank_item.record_count == 2
    assert blank_item.category_id == category.id


def test_save_all_model_dropdown_route_rejects_mismatched_fields(
    client, planner_user
):
    make_category(planner_user.id, "Products")
    login(client, planner_user.email, "Planner!Pass1234")

    response = client.post(PAGE + "/save-all", data={"models": ["ACO"]},
                           follow_redirects=True)

    assert "Model and category selections do not match." in response.get_data(as_text=True)
    assert ModelClassification.query.count() == 0


def test_csrf_protects_classification_writes(app, client, planner_user):
    login(client, planner_user.email, "Planner!Pass1234")
    original = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert client.post(PAGE + "/save", data={
            "model": "ACO", "category_id": "1"
        }).status_code == 400
        assert client.post(PAGE + "/remove", data={"model": "ACO"}).status_code == 400
        assert client.post(PAGE + "/save-all", data={
            "models": ["ACO"], "category_ids": ["1"]
        }).status_code == 400
        assert client.post(PAGE + "/categories/save", data={
            "name": "Accessories"
        }).status_code == 400
        assert ModelClassification.query.count() == 0
        assert ModelCategory.query.count() == 0
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
        "organise model names into categories",
    ):
        assert text in html
    assert 'href="' + PAGE + '"' in html


def test_category_editor_and_model_select_use_database_categories(
    client, planner_user
):
    category = make_category(planner_user.id, "Covers & Accessories")
    save_model_classification("Archived model", category.id, planner_user.id)
    login(client, planner_user.email, "Planner!Pass1234")
    html = client.get(PAGE).get_data(as_text=True)
    assert "Edit categories" in html
    assert 'data-category-dialog aria-labelledby="category-dialog-title"' in html
    assert "Different work content can have different demand" in html
    assert "Manage model categories" not in html
    assert "Covers &amp; Accessories" in html
    assert f'value="{category.id}" selected>' in html
    assert "Archived model" in html
    assert "classification-category-select" in html
    assert 'disabled title="Move assigned models before deleting this category"' in html
    assert html.index('id="model-summary-title"') < html.index(
        'id="category-definition-title"'
    ) < html.index('id="model-review-title"')
    assert (
        "Define categories around the work content, then review model assignments."
        in html
    )
    assert "table table-sm table-hover align-middle mb-0" in html
    assert "Saved assignments" in html
    assert "Current snapshot records" in html
    assert "Saved assignments can include model names or the blank-name group" in html
    assert "Different work content can have different demand" in html
    assert "full_unit" not in html
    assert "cover_only" not in html


def test_category_migration_preserves_existing_labels_and_supports_rollback():
    """Exercise both forecasting migrations with an isolated SQLite database."""
    migration_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    initial = runpy.run_path(str(
        migration_dir / "a7c31d9e2b60_add_forecast_model_classifications.py"
    ))
    configurable = runpy.run_path(str(
        migration_dir / "b84f6d2a91c3_make_forecast_model_categories_configurable.py"
    ))
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                initial["upgrade"]()
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
                configurable["upgrade"]()
            category_table = sa.Table(
                "forecast_model_categories",
                sa.MetaData(),
                autoload_with=connection,
            )
            cover_id = connection.execute(sa.select(category_table.c.id).where(
                category_table.c.legacy_value == "cover_only"
            )).scalar_one()
            converted = sa.Table(
                "forecast_model_classifications",
                sa.MetaData(),
                autoload_with=connection,
            )
            assert connection.execute(sa.select(converted.c.category_id)).scalar_one() == cover_id
            custom_id = connection.execute(category_table.insert().values(
                name="Customer-specific group",
                name_key="customer-specific group",
                description=None,
                sort_order=4,
                legacy_value=None,
            ).returning(category_table.c.id)).scalar_one()
            connection.execute(converted.update().values(category_id=custom_id))
            with connection.begin_nested():
                with pytest.raises(RuntimeError, match="custom category"):
                    with Operations.context(context):
                        configurable["downgrade"]()
            connection.execute(converted.update().values(category_id=cover_id))
            connection.execute(category_table.delete().where(
                category_table.c.id == custom_id
            ))
            with Operations.context(context):
                configurable["downgrade"]()
            restored = sa.Table(
                "forecast_model_classifications",
                sa.MetaData(),
                autoload_with=connection,
            )
            assert connection.execute(sa.select(restored.c.category)).scalar_one() == "cover_only"
            with Operations.context(context):
                initial["downgrade"]()
            assert "forecast_model_classifications" not in sa.inspect(
                connection
            ).get_table_names()
            assert "users" in sa.inspect(connection).get_table_names()
    finally:
        engine.dispose()
