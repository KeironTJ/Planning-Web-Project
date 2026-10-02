"""Readiness counts unique releases; completion, holds and coverage stay distinct."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.core.epicor_importers import REGISTRY
from app.sales.orders.models import ImportBatch
from app.transport.load_board import get_load_board
from app.transport.models import TransportOrderRelease
from app.transport.order_importer import TransportOrderImporter
from tests.test_transport_loads import baq_record, login_admin, sync


@pytest.fixture(autouse=True)
def ctx(app):
    with app.app_context():
        yield


def record(order=10001, load="LOAD-1", **overrides):
    return {
        "OrderHed_OrderNum": order, "OrderRel_OrderLine": 1, "OrderRel_OrderRelNum": 1,
        "OrderRel_CSGTransportNum_c": load, "Customer_Name": "Example customer",
        "OrderRel_PartNum": "SOFA-1", "Part_PartDescription": "Example sofa",
        "OrderRel_SellingReqQty": 1, "Calculated_JobStatus": "Complete",
        "Calculated_OrderStatus": "Not Complete", "JobProd_JobNum": f"{order}-1-1",
        "PartWip_BinNum": "BAY-01", "PartBin_DimCode": None,
        "ShipDtl_PackNum": None, "OrderHed_OrderHeld": False,
        "Customer_CreditHold": False, "Calculated_SOCreditHold": False,
        **overrides,
    }


def sync_orders(records):
    client = Mock()
    client.get_baq.return_value = records
    return TransportOrderImporter(client).run()


def readiness():
    return get_load_board()["stages"][1]["loads"][0]["readiness"]


def test_maps_releases_not_order_status_or_pack_presence():
    assert REGISTRY["transport_orders"] is TransportOrderImporter
    sync([baq_record(Calculated_OrderQty=1)])
    batch = sync_orders([record(ShipDtl_PackNum=87772)])
    release = TransportOrderRelease.query.one()
    assert batch.rows_inserted == 1
    assert release.production_state == "complete"  # Order status remains Not Complete.
    assert release.order_statuses == ["Not Complete"]
    assert release.pack_refs == ["87772"]
    assert release.jobs == ["10001-1-1"]
    assert release.customer == "Example customer"
    assert release.part_num == "SOFA-1"
    assert release.quantity == Decimal("1")
    assert readiness()["complete"] == 1
    assert readiness()["coverage_matches"]


def test_collapses_jobs_bins_and_pack_joins_without_summing_release_qty():
    sync([baq_record(Calculated_OrderQty=3)])
    first = record(OrderRel_SellingReqQty=3, ShipDtl_PackNum=1)
    second = record(OrderRel_SellingReqQty=3, JobProd_JobNum="OTHER-JOB",
                    Calculated_JobStatus="In Progress", PartWip_BinNum="UPHOL", ShipDtl_PackNum=2)
    batch = sync_orders([first, first.copy(), second])
    release = TransportOrderRelease.query.one()
    assert batch.row_count == 3
    assert batch.rows_inserted == 1
    assert release.quantity == 3
    assert release.production_state == "outstanding"
    assert release.locations == ["BAY-01", "UPHOL"]
    assert release.pack_refs == ["1", "2"]
    assert readiness()["count"] == 1
    assert readiness()["complete"] == 0
    assert readiness()["outstanding"] == 1
    assert readiness()["quantity"] == 3


def test_missing_job_status_does_not_imply_complete():
    sync([baq_record(Calculated_OrderQty=1)])
    sync_orders([record(), record(Calculated_JobStatus=None)])
    assert readiness()["complete"] == 0
    assert readiness()["unknown"] == 1


def test_completion_and_holds_are_separate_and_hold_counts_are_unique():
    sync([baq_record(Calculated_OrderQty=4)])
    sync_orders([
        record(1),
        record(2, Calculated_JobStatus="In Progress"),
        record(3, Calculated_JobStatus="No Job"),
        record(4, Customer_CreditHold=True, OrderHed_OrderHeld="true", Calculated_SOCreditHold=1),
    ])
    result = readiness()
    assert result["count"] == 4
    assert result["complete"] == 2
    assert result["outstanding"] == 1
    assert result["unknown"] == 1
    assert result["held"] == 1
    assert result["pct"] == 50
    assert result["coverage_matches"]
    assert [issue["order_num"] for issue in result["issues"]] == [2, 3, 4]
    assert result["issues"][-1]["holds"] == ["Order hold", "SO credit hold", "Customer credit hold"]


def test_unknown_hold_flags_and_missing_quantity_are_explicit():
    sync([baq_record(Calculated_OrderQty=1)])
    sync_orders([record(OrderRel_SellingReqQty=None, Customer_CreditHold=None)])
    result = readiness()
    assert result["complete"] == 1
    assert result["held"] == 0
    assert result["unchecked_holds"] == 1
    assert result["quantity"] is None
    assert not result["coverage_matches"]
    assert result["issues"][0]["hold_unknown"]


def test_unassigned_orders_excluded_and_reassignment_replaces_snapshot():
    first = record()
    batch = sync_orders([first, record(2, load="")])
    assert batch.row_count == 2
    assert batch.rows_inserted == 1
    assert "1 unassigned" in batch.notes
    sync([baq_record(Calculated_OrderQty=1), baq_record("LOAD-2", Calculated_OrderQty=1)])
    sync_orders([record(load="LOAD-2")])
    board = get_load_board()
    loads = {load["load_id"]: load for load in board["stages"][1]["loads"]}
    assert loads["LOAD-1"]["readiness"]["count"] == 0
    assert loads["LOAD-2"]["readiness"]["count"] == 1
    filtered = get_load_board(search="LOAD-2")
    assert filtered["summary"]["count"] == 1
    assert filtered["stages"][1]["loads"][0]["readiness"]["count"] == 1


@pytest.mark.parametrize("overrides", [
    {"OrderHed_OrderNum": None}, {"OrderRel_OrderLine": 1.5},
    {"OrderRel_OrderRelNum": -1}, {"OrderRel_SellingReqQty": "NaN"},
    {"OrderRel_SellingReqQty": -1}, {"Customer_CreditHold": "not-a-boolean"},
])
def test_bad_data_retains_previous_snapshot(overrides):
    sync_orders([record(1)])
    with pytest.raises(ValueError):
        sync_orders([record(2, **overrides)])
    assert TransportOrderRelease.query.one().order_num == 1
    assert ImportBatch.query.order_by(ImportBatch.id.desc()).first().status == "failed"


@pytest.mark.parametrize("overrides", [
    {"OrderRel_CSGTransportNum_c": "DIFFERENT-LOAD"},
    {"OrderRel_SellingReqQty": 2},
    {"OrderRel_PartNum": "DIFFERENT-PART"},
])
def test_conflicting_release_rows_abort_sync(overrides):
    sync_orders([record(9)])
    with pytest.raises(ValueError, match="conflicting"):
        sync_orders([record(1), record(1, **overrides)])
    assert TransportOrderRelease.query.one().order_num == 9


def test_empty_result_guard_and_missing_snapshot_never_show_ready():
    sync([baq_record(Calculated_OrderQty=2)])
    assert not readiness()["synced"]
    sync_orders([record()])
    with pytest.raises(RuntimeError, match="0 records"):
        sync_orders([])
    assert readiness()["count"] == 1
    assert not readiness()["coverage_matches"]
    assert get_load_board()["order_sync"]["latest"].status == "failed"


def test_rendering_preserves_compact_board_and_shows_coverage_holds_and_unknowns(client, admin_user):
    sync([baq_record(Calculated_OrderQty=5)])
    sync_orders([
        record(1, Calculated_JobStatus="In Progress", Customer_Name="<script>bad()</script>"),
        record(2, Customer_CreditHold=True),
        record(3, Calculated_JobStatus="No Job"),
    ])
    login_admin(client)
    html = client.get("/transport/loads").data.decode()
    assert "Production readiness" in html
    assert "1 / 3 releases job-complete" in html
    assert "1 awaiting production" in html
    assert "1 with unknown readiness" in html
    assert "1 releases on hold" in html
    assert "Check order coverage" in html
    assert "3 observed units / 5 load units" in html
    assert "SO 1 / 1 / 1" in html
    assert "Customer credit hold" in html
    assert "Location: BAY-01" in html
    assert "&lt;script&gt;bad()&lt;/script&gt;" in html
    assert "<script>bad()</script>" not in html
    assert "not packing or physical loading progress" in html
    assert "bg-success" not in html.split('class="load-readiness small mt-2"')[1].split("</details>")[0]


def test_no_orders_shipped_loads_failure_and_older_snapshot_are_honest(client, admin_user, db):
    sync([
        baq_record("EMPTY", Calculated_OrderQty=0),
        baq_record("MISSING", "Shipped", Calculated_OrderQty=5),
    ])
    sync_orders([record(load="UNRELATED")])
    batch = ImportBatch.query.filter_by(import_type="epicor_transport_orders").one()
    batch.uploaded_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.session.commit()
    login_admin(client)
    html = client.get("/transport/loads").data.decode()
    assert "No assigned order releases" in html
    assert "No matching order releases returned" in html
    assert "Order data is older than the load snapshot" in html
    assert "0 / 0 releases job-complete" not in html
    with pytest.raises(ValueError):
        sync_orders([record(Customer_CreditHold="bad")])
    html = client.get("/transport/loads").data.decode()
    assert "latest order-readiness sync failed" in html


def test_summary_readiness_is_release_weighted_and_follows_filters():
    sync([
        baq_record("ONE", Calculated_OrderQty=1),
        baq_record("THREE", Calculated_OrderQty=3),
        baq_record("PACKED", "Packed", Calculated_OrderQty=1),
        baq_record("OTHER", "On hold", Calculated_OrderQty=1),
    ])
    sync_orders([
        record(1, load="ONE"),
        record(2, load="THREE", Calculated_JobStatus="In Progress"),
        record(3, load="THREE", Calculated_JobStatus="No Job"),
        record(4, load="THREE", Customer_CreditHold=True),
        record(5, load="PACKED"),
        record(6, load="OTHER", Customer_CreditHold=None),
    ])
    board = get_load_board()
    scheduled = board["stages"][1]["readiness"]
    assert scheduled["count"] == 4
    assert scheduled["complete"] == 2
    assert scheduled["pct"] == 50  # Not the 66.7% average of 100% and 33.3%.
    assert scheduled["outstanding"] == 1
    assert scheduled["unknown"] == 1
    assert scheduled["held"] == 1
    assert not scheduled["clear"]
    assert board["stages"][2]["readiness"]["clear"]
    assert board["stages"][-1]["readiness"]["unchecked_holds"] == 1
    total = board["summary"]["readiness"]
    assert total["count"] == 6
    assert total["complete"] == 4
    assert total["pct"] == 66.7
    assert total["unchecked_holds"] == 1
    filtered = get_load_board(search="ONE")["summary"]["readiness"]
    assert filtered["count"] == 1
    assert filtered["pct"] == 100
    assert filtered["clear"]
    empty = get_load_board(search="no-match")["summary"]["readiness"]
    assert empty["synced"]
    assert empty["count"] == 0
    assert not empty["clear"]


def test_summary_coverage_mismatches_do_not_cancel_or_imply_ready(client, admin_user):
    sync([
        baq_record("SHORT", Calculated_OrderQty=2),
        baq_record("EXTRA", Calculated_OrderQty=0),
        baq_record("MISSING", "Shipped", Calculated_OrderQty=1),
    ])
    assert not get_load_board()["summary"]["readiness"]["synced"]
    sync_orders([record(1, load="SHORT"), record(2, load="EXTRA")])
    board = get_load_board()
    assert board["stages"][1]["readiness"]["pct"] == 100
    assert board["stages"][1]["readiness"]["coverage_issues"] == 2
    assert not board["stages"][1]["readiness"]["clear"]
    assert board["stages"][3]["readiness"]["count"] == 0
    assert board["stages"][3]["readiness"]["coverage_issues"] == 1
    assert board["summary"]["readiness"]["coverage_issues"] == 3
    login_admin(client)
    html = client.get("/transport/loads").data.decode()
    assert "2 / 2 observed releases job-complete" in html
    assert "Check order coverage: 2 loads" in html
    assert "No observed order releases" in html
    assert "0 / 0 observed releases job-complete" not in html
    with pytest.raises(ValueError):
        sync_orders([record(Customer_CreditHold="bad")])
    assert get_load_board()["summary"]["readiness"]["count"] == 2
    assert b"latest order-readiness sync failed" in client.get("/transport/loads").data
