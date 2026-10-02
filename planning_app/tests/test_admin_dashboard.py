from .conftest import login


def test_administration_heading_links_to_dashboard(client, admin_user):
    login(client, "admin@test.com", "Admin!Pass1234")

    response = client.get("/admin/")

    assert response.status_code == 200
    assert b'<a href="/admin/">\n                        <i class="bi bi-shield-lock me-1"></i>Administration' in response.data
