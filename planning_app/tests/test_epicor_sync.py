"""Network fetches must not hold the database's write lock."""

from unittest.mock import Mock

import pytest
import sqlalchemy as sa

from app import create_app
from app.config import TestingConfig
from app.core.epicor_importers import SalesOrderOpenImporter
from app.extensions import db
from app.forecasting.data_quality import save_va05_extract
from app.forecasting.models import ForecastDataRecord
from app.sales.orders.models import ImportBatch, SalesOrder
from app.transport.manifest_importer import TransportManifestImporter
from app.transport.models import TransportManifest
from tests.test_transport_manifests import manifest_row


def open_sales_row(order_num, job_num, assembly_seq=0):
    return {
        "OrderHed_OrderNum": order_num,
        "OrderDtl_OrderLine": 1,
        "OrderRel_OrderRelNum": 1,
        "OrderHed_OpenOrder": True,
        "JobProd_JobNum": job_num,
        "JobAsmbl_AssemblySeq": assembly_seq,
        "Calculated_ReleaseQty": 1,
        "Calculated_ReleasePriceGBP": 100,
        "OrderDtl_VoidLine": False,
    }


def test_open_sales_sync_reconciles_reopened_orders_without_changing_extracts(
    db_session, planner_user,
):
    db_session.add_all([
        SalesOrder(order_num=109149, order_line=1, rel_num=1, assembly_seq=0,
                   open_order=False, void_line=True, release_qty=0,
                   release_price_gbp=0),
        SalesOrder(order_num=109149, order_line=2, rel_num=1, assembly_seq=0,
                   open_order=None),
        SalesOrder(order_num=109149, order_line=1, rel_num=1, assembly_seq=0,
                   open_order=True, job_num="old", void_line=False,
                   release_qty=1, release_price_gbp=100),
        SalesOrder(order_num=200000, order_line=1, rel_num=1, open_order=False),
        SalesOrder(order_num=300000, order_line=1, rel_num=1, open_order=True),
    ])
    db_session.commit()
    before = save_va05_extract(planner_user.id)
    rows = [
        open_sales_row(109149, "job-1"),
        open_sales_row(109149, "job-1", assembly_seq=1),
        open_sales_row(109149, "job-2"),
        open_sales_row(109149, "job-2"),
    ]
    client = Mock()
    client.get_baq.return_value = rows

    batch = SalesOrderOpenImporter(client).run()
    stored = SalesOrder.query.order_by(SalesOrder.order_num, SalesOrder.job_num,
                                       SalesOrder.assembly_seq).all()
    assert [(row.order_num, row.job_num, row.assembly_seq) for row in stored] == [
        (109149, "job-1", 0), (109149, "job-1", 1),
        (109149, "job-2", 0), (200000, None, None),
    ]
    assert batch.rows_inserted == 3
    assert batch.notes == (
        "Deleted 2 stale open rows; reconciled 2 retained rows for open orders; "
        "1 duplicates skipped"
    )
    saved = ForecastDataRecord.query.filter_by(extract_id=before.id).all()
    assert len([r for r in saved if r.data["order_num"] == 109149]) == 3
    assert any("conflicting_qty" in r.findings for r in saved)
    after = save_va05_extract(planner_user.id)
    refreshed = ForecastDataRecord.query.filter_by(extract_id=after.id).all()
    assert all("conflicting_qty" not in r.findings for r in refreshed)

    SalesOrderOpenImporter(client).run()
    assert SalesOrder.query.count() == 4
    assert SalesOrder.query.filter_by(order_num=200000, open_order=False).count() == 1


def test_open_sales_sync_reconciles_more_than_one_order_batch(db_session):
    db_session.add_all([
        SalesOrder(order_num=num, order_line=1, rel_num=1, open_order=False)
        for num in range(1, 502)
    ])
    db_session.commit()
    client = Mock()
    client.get_baq.return_value = [
        open_sales_row(num, f"job-{num}") for num in range(1, 502)
    ]

    batch = SalesOrderOpenImporter(client).run()

    assert SalesOrder.query.count() == 501
    assert SalesOrder.query.filter_by(open_order=False).count() == 0
    assert "reconciled 501 retained rows" in batch.notes


@pytest.mark.parametrize("record", [
    {**open_sales_row(109149, "job"), "OrderHed_OpenOrder": False},
    {**open_sales_row(109149, "job"), "OrderHed_OpenOrder": None},
    {**open_sales_row(109149, "job"), "OrderHed_OrderNum": None},
])
def test_open_sales_sync_rejects_unsafe_responses(db_session, record):
    db_session.add(SalesOrder(
        order_num=109149, order_line=1, rel_num=1, open_order=False,
    ))
    db_session.commit()
    client = Mock()
    client.get_baq.return_value = [record]

    with pytest.raises(ValueError, match="missing order number or a non-open order"):
        SalesOrderOpenImporter(client).run()

    assert SalesOrder.query.filter_by(order_num=109149, open_order=False).count() == 1
    assert ImportBatch.query.filter_by(import_type="epicor_sales_open").one().status == "failed"


def test_open_sales_sync_rolls_back_reconciliation_on_insert_failure(db_session):
    db_session.add(SalesOrder(
        order_num=109149, order_line=1, rel_num=1, open_order=False,
    ))
    db_session.commit()
    client = Mock()
    client.get_baq.return_value = [{
        **open_sales_row(109149, "job"), "OrderDtl_OrderLine": None,
    }]

    with pytest.raises(sa.exc.IntegrityError):
        SalesOrderOpenImporter(client).run()

    assert SalesOrder.query.filter_by(order_num=109149, open_order=False).count() == 1
    assert ImportBatch.query.filter_by(import_type="epicor_sales_open").one().status == "failed"


@pytest.mark.parametrize("fetch_fails", [False, True])
def test_pending_audit_is_visible_and_another_writer_can_run_during_fetch(tmp_path, fetch_fails):
    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "sync.db")
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 0.1}}

    application = create_app(FileConfig)
    with application.app_context():
        db.create_all()
        client = Mock()

        def fetch(*args, **kwargs):
            with db.engine.begin() as connection:
                pending = connection.execute(sa.text(
                    "SELECT id, status FROM import_batches WHERE import_type = :kind"
                ), {"kind": TransportManifestImporter.IMPORT_TYPE}).one()
                assert pending.status == "pending"
                connection.execute(sa.text(
                    "UPDATE import_batches SET notes = 'Other writer succeeded' WHERE id = :id"
                ), {"id": pending.id})
            if fetch_fails:
                raise RuntimeError("Epicor unavailable")
            return [manifest_row()]

        client.get_baq.side_effect = fetch
        try:
            if fetch_fails:
                with pytest.raises(RuntimeError, match="Epicor unavailable"):
                    TransportManifestImporter(client).run(params={"DateFrom": "2026-10-01", "DateBefore": "2026-10-02"})
            else:
                TransportManifestImporter(client).run(params={"DateFrom": "2026-10-01", "DateBefore": "2026-10-02"})
            batch = ImportBatch.query.one()
            assert batch.status == ("failed" if fetch_fails else "success")
            assert TransportManifest.query.count() == (0 if fetch_fails else 1)
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
