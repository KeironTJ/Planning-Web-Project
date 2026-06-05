"""
Tests for the auth blueprint.

Covers: login, logout, registration, password validation, role checking.
"""

import pytest
from app.auth.models import User
from app.core.security import check_password_strength
from .conftest import login


class TestPasswordStrength:
    def test_strong_password_passes(self):
        ok, errors = check_password_strength("Secur3!Pass99")
        assert ok
        assert errors == []

    def test_short_password_fails(self):
        ok, errors = check_password_strength("Ab1!")
        assert not ok
        assert any("10 characters" in e for e in errors)

    def test_no_uppercase_fails(self):
        ok, errors = check_password_strength("secur3!pass99")
        assert not ok
        assert any("uppercase" in e for e in errors)

    def test_no_digit_fails(self):
        ok, errors = check_password_strength("Secur!Password")
        assert not ok
        assert any("digit" in e for e in errors)

    def test_no_special_fails(self):
        ok, errors = check_password_strength("SecurPass1234")
        assert not ok
        assert any("special" in e for e in errors)


class TestLogin:
    def test_login_success(self, client, planner_user):
        response = login(client, "planner@test.com", "Planner!Pass1234")
        assert response.status_code == 200
        # Should have been redirected to the Sales department portal
        assert b"Sales" in response.data or b"dashboard" in response.data.lower()

    def test_login_wrong_password(self, client, planner_user):
        response = login(client, "planner@test.com", "WrongPassword!")
        assert b"Invalid email or password" in response.data

    def test_login_unknown_email(self, client):
        response = login(client, "nobody@test.com", "SomePass1!")
        assert b"Invalid email or password" in response.data

    def test_login_inactive_user(self, client, db_session, viewer_user):
        viewer_user.is_active = False
        db_session.commit()
        response = login(client, "viewer@test.com", "Viewer!Pass1234")
        assert b"deactivated" in response.data

    def test_logout(self, client, planner_user):
        login(client, "planner@test.com", "Planner!Pass1234")
        response = client.get("/auth/logout", follow_redirects=True)
        assert b"signed out" in response.data


class TestRegistration:
    def test_register_success(self, client, db_session):
        response = client.post("/auth/register", data={
            "username": "newuser",
            "email": "newuser@test.com",
            "first_name": "New",
            "last_name": "User",
            "department": "Planning",
            "password": "NewUser!Pass1",
            "password_confirm": "NewUser!Pass1",
        }, follow_redirects=True)
        assert b"Registration successful" in response.data
        user = User.query.filter_by(email="newuser@test.com").first()
        assert user is not None
        assert user.first_name == "New"

    def test_register_duplicate_email(self, client, planner_user):
        response = client.post("/auth/register", data={
            "username": "unique_user",
            "email": "planner@test.com",   # already exists
            "password": "NewUser!Pass1",
            "password_confirm": "NewUser!Pass1",
        }, follow_redirects=True)
        assert b"already exists" in response.data

    def test_register_password_mismatch(self, client):
        response = client.post("/auth/register", data={
            "username": "mismatch_user",
            "email": "mismatch@test.com",
            "password": "NewUser!Pass1",
            "password_confirm": "Different!Pass1",
        }, follow_redirects=True)
        assert b"match" in response.data.lower()


class TestAccessControl:
    def test_protected_route_redirects_anonymous(self, client):
        response = client.get("/planning/capacity/", follow_redirects=False)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]

    def test_admin_route_forbidden_for_viewer(self, client, viewer_user):
        login(client, "viewer@test.com", "Viewer!Pass1234")
        response = client.get("/admin/", follow_redirects=False)
        assert response.status_code in (302, 403)

    def test_admin_accessible_for_admin(self, client, admin_user):
        login(client, "admin@test.com", "Admin!Pass1234")
        response = client.get("/admin/")
        assert response.status_code == 200


class TestUserModel:
    def test_set_and_check_password(self):
        user = User(username="test", email="t@t.com")
        user.set_password("MyPass!234")
        assert user.check_password("MyPass!234")
        assert not user.check_password("WrongPassword")

    def test_is_admin_property(self, admin_user):
        assert admin_user.is_admin is True

    def test_has_permission(self, planner_user):
        assert planner_user.has_permission("view_capacity")
        assert not planner_user.has_permission("manage_users")

    def test_full_name_fallback_to_username(self):
        user = User(username="johndoe")
        assert user.full_name == "johndoe"
