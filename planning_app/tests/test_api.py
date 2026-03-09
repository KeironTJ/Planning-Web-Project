"""
Tests for the REST API v1.

Covers: JWT authentication, work centre/order endpoints, permission checks.
"""

import json
import pytest
from .conftest import login


def get_token(client, email, password):
    """Obtain a JWT access token."""
    response = client.post("/api/v1/auth/token",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
    )
    data = response.get_json()
    return data.get("access_token")


class TestAPIAuth:
    def test_obtain_token_success(self, client, planner_user):
        response = client.post("/api/v1/auth/token",
            data=json.dumps({"email": "planner@test.com", "password": "Planner!Pass1234"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["username"] == "planner_test"

    def test_obtain_token_wrong_credentials(self, client, planner_user):
        response = client.post("/api/v1/auth/token",
            data=json.dumps({"email": "planner@test.com", "password": "WrongPass"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_me_endpoint(self, client, planner_user):
        token = get_token(client, "planner@test.com", "Planner!Pass1234")
        response = client.get("/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["email"] == "planner@test.com"

    def test_protected_endpoint_without_token(self, client):
        response = client.get("/api/v1/capacity/work-centres")
        assert response.status_code == 401


class TestWorkCentresAPI:
    def test_list_work_centres(self, client, planner_user):
        token = get_token(client, "planner@test.com", "Planner!Pass1234")
        response = client.get("/api/v1/capacity/work-centres",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert isinstance(response.get_json(), list)

    def test_create_work_centre(self, client, planner_user):
        token = get_token(client, "planner@test.com", "Planner!Pass1234")
        response = client.post("/api/v1/capacity/work-centres",
            data=json.dumps({
                "code": "API01",
                "name": "API Test WC",
                "hours_per_shift": 8,
                "shifts_per_day": 1,
                "efficiency_pct": 85,
            }),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        data = response.get_json()
        assert data["code"] == "API01"

    def test_viewer_cannot_create_work_centre(self, client, viewer_user):
        token = get_token(client, "viewer@test.com", "Viewer!Pass1234")
        response = client.post("/api/v1/capacity/work-centres",
            data=json.dumps({"code": "DENY01", "name": "Denied", "hours_per_shift": 8,
                             "shifts_per_day": 1, "efficiency_pct": 85}),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


class TestWorkOrdersAPI:
    def test_list_work_orders(self, client, planner_user):
        token = get_token(client, "planner@test.com", "Planner!Pass1234")
        response = client.get("/api/v1/capacity/work-orders",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "items" in data
        assert "total" in data

    def test_get_nonexistent_order(self, client, planner_user):
        token = get_token(client, "planner@test.com", "Planner!Pass1234")
        response = client.get("/api/v1/capacity/work-orders/99999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
