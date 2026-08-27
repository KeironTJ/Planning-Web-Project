from app.admin.models import SyncJob, SyncJobItem
from .conftest import login


def test_reorder_persists_visible_order_when_existing_sort_orders_are_duplicated(
    client, db_session, admin_user
):
    job = SyncJob(name="Test sync")
    items = [
        SyncJobItem(job=job, importer_key="stock", sort_order=0),
        SyncJobItem(job=job, importer_key="purchase_orders", sort_order=0),
        SyncJobItem(job=job, importer_key="sales_open", sort_order=0),
    ]
    db_session.add(job)
    db_session.commit()
    login(client, "admin@test.com", "Admin!Pass1234")

    requested_order = [items[1].id, items[2].id, items[0].id]
    response = client.post(
        f"/admin/epicor-sync/schedules/jobs/{job.id}/items/{items[0].id}",
        json={"action": "reorder", "item_ids": requested_order},
    )

    assert response.status_code == 200
    persisted = (
        SyncJobItem.query.filter_by(job_id=job.id)
        .order_by(SyncJobItem.sort_order)
        .all()
    )
    assert [item.id for item in persisted] == requested_order
    assert [item.sort_order for item in persisted] == [0, 1, 2]
