"""Manual and scheduled manifest ranges use the same validation and semantics."""

import json
from unittest.mock import Mock, patch

import pytest

from app.admin.models import SyncJob, SyncJobItem
from app.core.scheduler import _resolve_item_params
from app.transport.manifest_importer import manifest_date_params
from tests.conftest import login


DATES = {"DateFrom": "2026-09-01", "DateBefore": "2026-10-01"}


@pytest.fixture
def authenticated(client, admin_user):
    login(client, "admin@test.com", "Admin!Pass1234")
    return client


@pytest.mark.parametrize("params", [
    [], {"mode": "today"}, {"mode": "range"}, {"DateFrom": "bad", "DateBefore": "bad"},
    {"DateFrom": "2026-10-01"}, {"DateFrom": "2026-10-01", "DateBefore": "2026-10-01"},
    {"DateFrom": "2026-10-02", "DateBefore": "2026-10-01"},
    {"mode": "auto", **DATES}, {"$filter": "something"},
])
def test_invalid_manual_ranges_rejected_before_network(authenticated, params):
    with patch("app.core.epicor_client.KineticClient.from_app") as network:
        response = authenticated.post("/admin/epicor-sync/run-one", json={
            "baq_key": "transport_manifest", "params": params,
        })
    assert response.status_code == 400
    assert response.json["status"] == "error"
    network.assert_not_called()


@pytest.mark.parametrize("params,expected", [
    ({"mode": "auto"}, None), ({}, None),
    ({"mode": "range", **DATES}, DATES), (DATES, DATES),
])
def test_manual_params_reach_importer_without_mode(authenticated, params, expected):
    batch = Mock(row_count=10, rows_inserted=0, notes="Test scope")
    with patch("app.core.epicor_client.KineticClient.from_app"), \
         patch("app.transport.manifest_importer.TransportManifestImporter.run", return_value=batch) as run:
        response = authenticated.post("/admin/epicor-sync/run-one", json={
            "baq_key": "transport_manifest", "params": params,
        })
    assert response.status_code == 200
    assert run.call_args.kwargs["params"] == expected


def test_form_fallback_passes_dates_and_rejects_invalid(authenticated):
    with patch("app.core.epicor_client.KineticClient.from_app"), \
         patch("app.transport.manifest_importer.TransportManifestImporter.run",
               return_value=Mock(row_count=1, rows_inserted=0)) as run:
        response = authenticated.post("/admin/epicor-sync/run", data={
            "baq_key": "transport_manifest", "mode": "range", **DATES,
        })
    assert response.status_code == 302
    assert run.call_args.kwargs["params"] == DATES
    with patch("app.core.epicor_client.KineticClient.from_app") as network:
        response = authenticated.post("/admin/epicor-sync/run", data={
            "baq_key": "transport_manifest", "mode": "range", "DateFrom": "bad",
        })
    assert response.status_code == 302
    network.assert_not_called()


def test_scheduled_fixed_range_persists_resolves_and_auto_clears(authenticated, db_session):
    job = SyncJob(name="Manifest test", enabled=False)
    item = SyncJobItem(job=job, importer_key="transport_manifest", sort_order=0)
    db_session.add(job)
    db_session.commit()
    url = f"/admin/epicor-sync/schedules/jobs/{job.id}/items/{item.id}"
    response = authenticated.post(url, json={
        "action": "save_params", "schedule_params": {"mode": "range", **DATES},
    })
    assert response.status_code == 200
    assert item.parsed_params == {"mode": "range", **DATES}
    assert _resolve_item_params(item) == DATES
    assert response.json["params_label"] == "2026-09-01 to before 2026-10-01"
    original = item.schedule_params
    response = authenticated.post(url, json={
        "action": "save_params", "schedule_params": {"mode": "range", "DateFrom": "bad"},
    })
    assert response.status_code == 400
    assert item.schedule_params == original
    response = authenticated.post(url, json={"action": "save_params", "schedule_params": None})
    assert response.status_code == 200
    assert item.schedule_params is None
    assert _resolve_item_params(item) is None
    assert response.json["params_label"] == "Auto (-90 / +90 days)"
    assert not job.enabled


def test_old_malformed_schedule_is_rejected_at_execution():
    item = SyncJobItem(importer_key="transport_manifest",
                       schedule_params=json.dumps({"mode": "range", "DateFrom": "bad"}))
    with pytest.raises(ValueError, match="both required"):
        _resolve_item_params(item)


def test_sync_and_schedule_controls_render(authenticated, db_session):
    job = SyncJob(name="Manifest test")
    item = SyncJobItem(job=job, importer_key="transport_manifest")
    db_session.add(job)
    db_session.commit()
    html = authenticated.get("/admin/epicor-sync").get_data(as_text=True)
    assert 'id="manifestSyncMode"' in html
    assert 'name="DateBefore"' in html
    assert "DateBefore (exclusive)" in html
    assert "Auto today:" in html
    html = authenticated.get("/admin/epicor-sync/schedules").get_data(as_text=True)
    assert f'id="manifest-range-{item.id}"' in html
    assert "Fixed ranges repeat unchanged" in html
    assert "DateBefore (exclusive)" in html


def test_validator_auto_and_fixed_modes():
    assert manifest_date_params({}) == {}
    assert manifest_date_params({"mode": "auto"}) == {}
    assert manifest_date_params({"mode": "range", **DATES}) == DATES
