"""
Tests for materials service layer -- netting, status classification, PO lead-time.

Priority: _row_status() is the single source of truth for the 5-tier material
status; every badge, KPI card, and summary in the app derives from it.
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal

from app.purchasing.materials.services.netting import _row_status
from app.purchasing.materials.models import (
    MaterialRequirementMain,
    Stock,
    PurchaseOrder,
    MrpExemptMaterial,
)
from app.extensions import db as _db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TODAY = date.today()


def D(v) -> Decimal:
    return Decimal(str(v))


def make_req(
    material_code="FAB001",
    qty_for_order=100,
    qty_issued=0,
    due_date=None,
    job_released=False,
    job_firm=False,
    material_group="fabric",
    class_id="A101",
    works_order="WO001",
    so_number="1001",
):
    req = MaterialRequirementMain(
        material_code=material_code,
        material_description="Test Fabric",
        qty_for_order=D(qty_for_order),
        qty_issued=D(qty_issued),
        due_date=due_date or (TODAY + timedelta(days=30)),
        job_released=job_released,
        job_firm=job_firm,
        job_closed=False,
        issued_complete=False,
        material_group=material_group,
        class_id=class_id,
        works_order=works_order,
        so_number=so_number,
        warehouse_code="STORES",
    )
    _db.session.add(req)
    return req


def make_stock(
    part_num="FAB001",
    qty_on_hand=0,
    plant="STORES",
    qty_on_hand_stores=None,
    qty_on_hand_prod_uk=0,
    qty_on_hand_romania=0,
    qty_on_hand_others=0,
):
    if qty_on_hand_stores is None:
        qty_on_hand_stores = qty_on_hand
    s = Stock(
        part_num=part_num,
        qty_on_hand=D(qty_on_hand),
        plant=plant,
        qty_on_hand_stores=D(qty_on_hand_stores),
        qty_on_hand_prod_uk=D(qty_on_hand_prod_uk),
        qty_on_hand_romania=D(qty_on_hand_romania),
        qty_on_hand_others=D(qty_on_hand_others),
    )
    _db.session.add(s)
    return s


def make_po(part_num="FAB001", outstanding_qty=0, due_date=None,
            po_num=1, po_line=1, po_release=1, unit_cost=1,
            cost_per_code="E", supplier_name="Test Supplier",
            line_desc=None, unit_of_measure="M", rel_qty=None, received_qty=0):
    if rel_qty is None:
        rel_qty = outstanding_qty
    po = PurchaseOrder(
        po_num=po_num, po_line=po_line, po_release=po_release,
        part_num=part_num,
        outstanding_qty=D(outstanding_qty),
        rel_qty=D(rel_qty),
        received_qty=D(received_qty),
        line_desc=line_desc,
        unit_of_measure=unit_of_measure,
        due_date=due_date or (TODAY + timedelta(days=5)),
        unit_cost=D(unit_cost),
        cost_per_code=cost_per_code,
        supplier_name=supplier_name,
        open_order=True, open_line=True, open_release=True,
    )
    _db.session.add(po)
    return po


@pytest.fixture
def zero_lead_days(db):
    """Set MRP lead days to 0 so a PO only needs to arrive by req due date."""
    from app.admin.models import SystemSetting
    SystemSetting.set("mrp_material_lead_days", "0")
    _db.session.commit()


def run_report(**kwargs):
    from app.purchasing.materials.services.netting import get_shortage_report
    _db.session.commit()
    return get_shortage_report(material_group="fabric", shortages_only=False, **kwargs)


def run_pegging(**kwargs):
    from app.purchasing.materials.services.pegging import get_mrp_pegging
    _db.session.commit()
    return get_mrp_pegging(**kwargs)


# ---------------------------------------------------------------------------
# Unit tests: _row_status() -- pure function, no DB required
# ---------------------------------------------------------------------------

class TestRowStatus:
    """All 6 code paths through the 5-tier status decision function."""

    def test_zero_net_is_ok(self):
        assert _row_status(D(0), D(0), D(100), False, False) == "ok"

    def test_zero_net_with_po_still_ok(self):
        assert _row_status(D(0), D(0), D(0), True, True) == "ok"

    def test_stock_exactly_covers_is_ok(self):
        assert _row_status(D(50), D(0), D(50), False, False) == "ok"

    def test_stock_overcoverage_is_ok(self):
        assert _row_status(D(30), D(0), D(100), True, False) == "ok"

    def test_gap_covered_by_po_unreleased_is_low_risk(self):
        # Stock < net_required; shortage == 0 (PO fills gap); job not released
        assert _row_status(D(100), D(0), D(20), False, True) == "low_risk"

    def test_gap_covered_by_po_released_is_med_risk(self):
        # Same gap; job IS in production -- higher urgency
        assert _row_status(D(100), D(0), D(20), True, True) == "med_risk"

    def test_genuine_shortage_with_po_is_late_po(self):
        assert _row_status(D(100), D(10), D(0), False, True) == "late_po"

    def test_genuine_shortage_no_po_is_high_risk(self):
        assert _row_status(D(100), D(10), D(0), False, False) == "high_risk"

    def test_shortage_overrides_release_status(self):
        # Released job with genuine shortage -> late_po, not med_risk
        assert _row_status(D(100), D(10), D(0), True, True) == "late_po"

    def test_shortage_no_po_released_is_high_risk(self):
        assert _row_status(D(100), D(10), D(0), True, False) == "high_risk"

    def test_released_none_treated_as_not_released(self):
        # released=None -> not released -> low_risk, not med_risk
        assert _row_status(D(100), D(0), D(20), None, True) == "low_risk"


# ---------------------------------------------------------------------------
# Integration tests: netting with zero lead days (dates kept simple)
# ---------------------------------------------------------------------------

class TestNettingIntegration:
    """Cumulative MRP netting with real DB data. Uses zero_lead_days fixture."""

    @pytest.fixture(autouse=True)
    def ctx(self, app, zero_lead_days):
        with app.test_request_context():
            yield

    def test_stock_fully_covers(self, db):
        make_req("FAB001", qty_for_order=50)
        make_stock("FAB001", qty_on_hand=100)
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB001"]
        assert rows[0].status == "ok" and rows[0].shortage == D(0)

    def test_qc_other_stock_does_not_cover_requirement(self, db):
        make_req("FAB001-QC", qty_for_order=50)
        make_stock(
            "FAB001-QC",
            qty_on_hand=100,
            qty_on_hand_stores=0,
            qty_on_hand_prod_uk=0,
            qty_on_hand_romania=0,
            qty_on_hand_others=100,
        )
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB001-QC"]
        assert rows[0].stock_on_hand == D(0)
        assert rows[0].status == "high_risk" and rows[0].shortage == D(50)

    def test_available_buckets_are_summed_for_requirement_cover(self, db):
        make_req("FAB001-AVAIL", qty_for_order=50)
        make_stock(
            "FAB001-AVAIL",
            qty_on_hand=100,
            qty_on_hand_stores=10,
            qty_on_hand_prod_uk=15,
            qty_on_hand_romania=25,
            qty_on_hand_others=50,
        )
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB001-AVAIL"]
        assert rows[0].stock_on_hand == D(50)
        assert rows[0].status == "ok" and rows[0].shortage == D(0)

    def test_no_stock_no_po_is_high_risk(self, db):
        make_req("FAB002", qty_for_order=50)
        make_stock("FAB002", qty_on_hand=0)
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB002"]
        assert rows[0].status == "high_risk" and rows[0].shortage == D(50)

    def test_po_covers_gap_unreleased_is_low_risk(self, db):
        due = TODAY + timedelta(days=30)
        make_req("FAB003", qty_for_order=100, job_released=False, due_date=due)
        make_stock("FAB003", qty_on_hand=20)
        make_po("FAB003", outstanding_qty=80, due_date=due)
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB003"]
        assert rows[0].status == "low_risk"

    def test_po_covers_gap_released_is_med_risk(self, db):
        due = TODAY + timedelta(days=30)
        make_req("FAB004", qty_for_order=100, job_released=True, due_date=due)
        make_stock("FAB004", qty_on_hand=20)
        make_po("FAB004", outstanding_qty=80, due_date=due)
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB004"]
        assert rows[0].status == "med_risk"

    def test_insufficient_po_is_late_po(self, db):
        due = TODAY + timedelta(days=30)
        make_req("FAB005", qty_for_order=100, due_date=due)
        make_stock("FAB005", qty_on_hand=0)
        make_po("FAB005", outstanding_qty=50, due_date=due)
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB005"]
        assert rows[0].status == "late_po" and rows[0].shortage > D(0)

    def test_cumulative_stock_consumed_by_first_req(self, db):
        """Earlier req consumes stock; later req covered by PO."""
        early = TODAY + timedelta(days=10)
        late = TODAY + timedelta(days=30)
        make_req("FAB006", qty_for_order=80, due_date=early, so_number="2001")
        make_req("FAB006", qty_for_order=50, due_date=late, so_number="2002", works_order="WO002")
        make_stock("FAB006", qty_on_hand=80)
        make_po("FAB006", outstanding_qty=50, due_date=late)
        by_so = {r.so_number: r for r in run_report()["rows"] if r.material_code == "FAB006"}
        assert by_so["2001"].status == "ok"
        assert by_so["2002"].status == "low_risk"

    def test_second_req_has_nothing_left(self, db):
        """First req uses all stock; second has no PO -> high_risk."""
        early = TODAY + timedelta(days=10)
        late = TODAY + timedelta(days=30)
        make_req("FAB007", qty_for_order=100, due_date=early, so_number="3001")
        make_req("FAB007", qty_for_order=50, due_date=late, so_number="3002", works_order="WO003")
        make_stock("FAB007", qty_on_hand=100)
        by_so = {r.so_number: r for r in run_report()["rows"] if r.material_code == "FAB007"}
        assert by_so["3001"].status == "ok"
        assert by_so["3002"].status == "high_risk"

    def test_exempt_material_excluded(self, db):
        make_req("EXEMPT01", qty_for_order=100)
        make_stock("EXEMPT01", qty_on_hand=0)
        _db.session.add(MrpExemptMaterial(material_code="EXEMPT01", reason="Test"))
        assert not [r for r in run_report()["rows"] if r.material_code == "EXEMPT01"]

    def test_fully_issued_has_zero_shortage(self, db):
        make_req("FAB008", qty_for_order=50, qty_issued=50)
        make_stock("FAB008", qty_on_hand=0)
        for r in [r for r in run_report()["rows"] if r.material_code == "FAB008"]:
            assert r.net_required == D(0) and r.shortage == D(0)


class TestMrpPeggingFilters:
    @pytest.fixture(autouse=True)
    def ctx(self, app, zero_lead_days):
        with app.test_request_context():
            yield

    def test_filters_sales_order_materials_by_group(self, db):
        make_req("FAB-FILTER", material_group="fabric", so_number="FILTER-SO")
        make_req(
            "COMP-FILTER",
            material_group="component",
            so_number="FILTER-SO",
            works_order="WO-COMP",
        )

        all_codes = {
            m.material_code
            for m in run_pegging(so_number="FILTER-SO")["materials"]
        }
        fabric_codes = {
            m.material_code
            for m in run_pegging(so_number="FILTER-SO", material_group="fabric")["materials"]
        }
        component_codes = {
            m.material_code
            for m in run_pegging(so_number="FILTER-SO", material_group="component")["materials"]
        }

        assert all_codes == {"FAB-FILTER", "COMP-FILTER"}
        assert fabric_codes == {"FAB-FILTER"}
        assert component_codes == {"COMP-FILTER"}

    def test_so_status_is_separate_from_global_material_status(self, db):
        make_req(
            "COMP-SCOPE",
            qty_for_order=2,
            due_date=TODAY + timedelta(days=5),
            material_group="component",
            so_number="SELECTED-SO",
            works_order="SELECTED-JOB",
        )
        make_req(
            "COMP-SCOPE",
            qty_for_order=5,
            due_date=TODAY + timedelta(days=10),
            material_group="component",
            so_number="OTHER-SO",
            works_order="OTHER-JOB",
        )
        make_stock("COMP-SCOPE", qty_on_hand=5)

        result = run_pegging(so_number="SELECTED-SO", material_group="component")
        material = next(m for m in result["materials"] if m.material_code == "COMP-SCOPE")
        selected_events = [e for e in material.events if e.so_number == "SELECTED-SO"]

        assert material.selected_so_status == "ok"
        assert material.mat_status == "high_risk"
        assert len(selected_events) == 1
        assert selected_events[0].reference == "SELECTED-JOB"

    def test_pegging_exposes_stock_bucket_breakdown(self, db):
        make_req(
            "PEG-STOCK",
            qty_for_order=50,
            so_number="PEG-SO",
            works_order="PEG-JOB",
        )
        make_stock(
            "PEG-STOCK",
            qty_on_hand=100,
            qty_on_hand_stores=10,
            qty_on_hand_prod_uk=15,
            qty_on_hand_romania=25,
            qty_on_hand_others=50,
        )

        result = run_pegging(so_number="PEG-SO")
        material = next(m for m in result["materials"] if m.material_code == "PEG-STOCK")

        assert material.opening_stock == D(50)
        assert material.stock_breakdown.available == D(50)
        assert material.stock_breakdown.stores == D(10)
        assert material.stock_breakdown.prod_uk == D(15)
        assert material.stock_breakdown.romania == D(25)
        assert material.stock_breakdown.others == D(50)
        assert material.stock_breakdown.total == D(100)

    def test_component_scope_excludes_unconfigured_classes(self, db):
        from app.admin.models import SystemSetting, SETTING_COMPONENT_CLASS_IDS

        SystemSetting.set(SETTING_COMPONENT_CLASS_IDS, "P101")
        make_req(
            "COMP-IN-SCOPE",
            material_group="component",
            class_id="P101",
            so_number="CLASS-SO",
        )
        make_req(
            "COMP-OUT-OF-SCOPE",
            material_group="component",
            class_id="G101",
            so_number="CLASS-SO",
            works_order="WO-OUT",
        )

        result = run_pegging(so_number="CLASS-SO", material_group="component")

        assert {m.material_code for m in result["materials"]} == {"COMP-IN-SCOPE"}


# ---------------------------------------------------------------------------
# Integration tests: PO lead-time behaviour
# ---------------------------------------------------------------------------

class TestPoLeadTime:
    """POs must arrive >= lead_days before req due date to count as coverage."""

    @pytest.fixture(autouse=True)
    def ctx(self, app):
        with app.test_request_context():
            yield

    def test_po_due_same_day_as_req_not_counted_with_14day_lead(self, db):
        req_due = TODAY + timedelta(days=30)
        make_req("FAB009", qty_for_order=100, due_date=req_due)
        make_stock("FAB009", qty_on_hand=0)
        make_po("FAB009", outstanding_qty=100, due_date=req_due)
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB009"]
        assert rows[0].status == "late_po"

    def test_po_arriving_before_lead_deadline_gives_coverage(self, db):
        req_due = TODAY + timedelta(days=30)
        po_due = req_due - timedelta(days=15)  # 1 day before 14-day deadline
        make_req("FAB010", qty_for_order=100, due_date=req_due)
        make_stock("FAB010", qty_on_hand=0)
        make_po("FAB010", outstanding_qty=100, due_date=po_due)
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB010"]
        assert rows[0].status == "low_risk"

    def test_overdue_po_clamped_to_today_covers_future_req(self, db, zero_lead_days):
        req_due = TODAY + timedelta(days=30)
        yesterday = TODAY - timedelta(days=1)
        make_req("FAB011", qty_for_order=100, due_date=req_due)
        make_stock("FAB011", qty_on_hand=0)
        make_po("FAB011", outstanding_qty=100, due_date=yesterday)
        rows = [r for r in run_report()["rows"] if r.material_code == "FAB011"]
        assert rows[0].status in ("low_risk", "med_risk")

    def test_exhausted_po_gives_high_risk_not_late_po(self, db, zero_lead_days):
        """PO fully consumed by earlier req; remaining req has no PO -> high_risk."""
        early = TODAY + timedelta(days=10)
        late = TODAY + timedelta(days=30)
        make_req("FAB012", qty_for_order=100, due_date=early, so_number="4001")
        make_req("FAB012", qty_for_order=50, due_date=late, so_number="4002", works_order="WO004")
        make_stock("FAB012", qty_on_hand=0)
        make_po("FAB012", outstanding_qty=100, due_date=early)
        by_so = {r.so_number: r for r in run_report()["rows"] if r.material_code == "FAB012"}
        assert by_so["4001"].status in ("low_risk", "med_risk")
        assert by_so["4002"].status == "high_risk"


# ---------------------------------------------------------------------------
# Integration tests: get_so_material_status()
# ---------------------------------------------------------------------------

class TestSoMaterialStatus:
    """Aggregate status = worst-case across all req lines for an SO."""

    @pytest.fixture(autouse=True)
    def ctx(self, app, zero_lead_days):
        with app.test_request_context():
            yield

    def test_unknown_so_gives_no_data(self, db):
        from app.purchasing.materials.services.status import get_so_material_status
        _db.session.commit()
        assert get_so_material_status(["9999"])["9999"] == "no_data"

    def test_all_covered_lines_gives_ok(self, db):
        make_req("FAB013", qty_for_order=50, so_number="5001", works_order="WO013")
        make_req("FAB014", qty_for_order=30, so_number="5001", works_order="WO014")
        make_stock("FAB013", qty_on_hand=50)
        make_stock("FAB014", qty_on_hand=30)
        from app.purchasing.materials.services.status import get_so_material_status
        _db.session.commit()
        assert get_so_material_status(["5001"])["5001"] == "ok"

    def test_worst_case_status_wins(self, db):
        """One OK line + one high_risk line -> high_risk for the SO."""
        make_req("FAB015", qty_for_order=50, so_number="6001", works_order="WO015")
        make_req("FAB016", qty_for_order=50, so_number="6001", works_order="WO016")
        make_stock("FAB015", qty_on_hand=50)   # ok
        make_stock("FAB016", qty_on_hand=0)    # high_risk
        from app.purchasing.materials.services.status import get_so_material_status
        _db.session.commit()
        assert get_so_material_status(["6001"])["6001"] == "high_risk"

    def test_empty_list_returns_empty(self, db):
        from app.purchasing.materials.services.status import get_so_material_status
        assert get_so_material_status([]) == {}


# ---------------------------------------------------------------------------
# Integration tests: exemption mutations
# ---------------------------------------------------------------------------

class TestExemptions:
    """add_exemptions / remove_exemptions mutate the list used by netting."""

    def test_add_new_codes(self, db):
        from app.purchasing.materials.services.exempt import add_exemptions, get_exempt_materials
        result = add_exemptions(["EX001", "EX002"], reason="No PO raised", user_id=None)
        assert result == {"added": 2, "skipped": 0}
        codes = {m.material_code for m in get_exempt_materials()}
        assert "EX001" in codes and "EX002" in codes

    def test_add_duplicate_is_skipped(self, db):
        from app.purchasing.materials.services.exempt import add_exemptions
        add_exemptions(["EX003"], reason="First", user_id=None)
        result = add_exemptions(["EX003"], reason="Second", user_id=None)
        assert result == {"added": 0, "skipped": 1}

    def test_add_normalises_to_uppercase(self, db):
        from app.purchasing.materials.services.exempt import add_exemptions, get_exempt_materials
        add_exemptions(["fab-lower"], reason=None, user_id=None)
        codes = {m.material_code for m in get_exempt_materials()}
        assert "FAB-LOWER" in codes

    def test_add_ignores_blank_entries(self, db):
        from app.purchasing.materials.services.exempt import add_exemptions
        result = add_exemptions(["", "  ", "VALID01"], reason=None, user_id=None)
        assert result["added"] == 1

    def test_remove_existing_codes(self, db):
        from app.purchasing.materials.services.exempt import add_exemptions, remove_exemptions, get_exempt_materials
        add_exemptions(["REM001", "REM002"], reason=None, user_id=None)
        deleted = remove_exemptions(["REM001"])
        assert deleted == 1
        codes = {m.material_code for m in get_exempt_materials()}
        assert "REM001" not in codes and "REM002" in codes

    def test_remove_nonexistent_returns_zero(self, db):
        from app.purchasing.materials.services.exempt import remove_exemptions
        assert remove_exemptions(["DOES_NOT_EXIST"]) == 0

    def test_exempted_code_excluded_from_netting(self, db, app, zero_lead_days):
        """An exempted material must not appear in shortage results."""
        with app.test_request_context():
            make_req("EXTEST01", qty_for_order=100)
            make_stock("EXTEST01", qty_on_hand=0)
            from app.purchasing.materials.services.exempt import add_exemptions
            add_exemptions(["EXTEST01"], reason="Test", user_id=None)
            assert not [r for r in run_report()["rows"] if r.material_code == "EXTEST01"]


# ---------------------------------------------------------------------------
# Integration tests: shortage report filters
# ---------------------------------------------------------------------------

class TestShortageFilters:
    """URL-driven filters applied after netting (dept, so_filter, due_before)."""

    @pytest.fixture(autouse=True)
    def ctx(self, app, zero_lead_days):
        with app.test_request_context():
            yield

    def test_so_filter_scopes_to_single_so(self, db):
        make_req("FAB101", qty_for_order=50, so_number="SO001")
        make_req("FAB102", qty_for_order=50, so_number="SO002")
        make_stock("FAB101", qty_on_hand=0)
        make_stock("FAB102", qty_on_hand=0)
        rows = run_report(so_filter="SO001")["rows"]
        assert all(r.so_number == "SO001" for r in rows)
        assert not any(r.so_number == "SO002" for r in rows)

    def test_due_before_excludes_later_reqs(self, db):
        cutoff = TODAY + timedelta(days=20)
        make_req("FAB103", qty_for_order=50, due_date=TODAY + timedelta(days=10))
        make_req("FAB104", qty_for_order=50, due_date=TODAY + timedelta(days=30))
        make_stock("FAB103", qty_on_hand=0)
        make_stock("FAB104", qty_on_hand=0)
        rows = run_report(due_before=cutoff)["rows"]
        codes = {r.material_code for r in rows}
        assert "FAB103" in codes and "FAB104" not in codes

    def test_shortages_only_hides_ok_rows(self, db):
        make_req("FAB105", qty_for_order=50)
        make_stock("FAB105", qty_on_hand=100)   # fully covered -> ok
        make_req("FAB106", qty_for_order=50, works_order="WO106")
        make_stock("FAB106", qty_on_hand=0)    # uncovered -> high_risk
        from app.purchasing.materials.services.netting import get_shortage_report
        _db.session.commit()
        shortages = get_shortage_report(material_group="fabric", shortages_only=True)["rows"]
        codes = {r.material_code for r in shortages}
        assert "FAB105" not in codes   # ok row hidden
        assert "FAB106" in codes       # shortage row shown


# ---------------------------------------------------------------------------
# Release impact scenarios
# ---------------------------------------------------------------------------

class TestReleaseImpact:
    @pytest.fixture(autouse=True)
    def ctx(self, app, zero_lead_days):
        with app.test_request_context():
            yield

    def run_impact(self, committed_keys=None):
        from app.purchasing.materials.services.release_impact import get_release_impact
        _db.session.commit()
        return get_release_impact(committed_keys=committed_keys)

    def test_single_po_unlocks_fully_covered_job(self, db):
        make_req("FAB201", qty_for_order=10, works_order="WO201", so_number="2201")
        make_stock("FAB201", qty_on_hand=0)
        make_po(
            "FAB201", outstanding_qty=10, po_num=201,
            unit_cost=5, supplier_name="Fabric Co",
        )

        data = self.run_impact()

        result = next(row for row in data["single_results"] if row["key"] == "201/1/1")
        assert result["jobs_unlocked"] == ["WO201"]
        assert result["cash_proxy"] == D(50)
        assert result["result"] == "Releases production"

    def test_bundle_finds_job_not_unlocked_by_either_po_alone(self, db):
        make_req("FAB202", qty_for_order=10, works_order="WO202", so_number="2202")
        make_req(
            "COMP202", qty_for_order=4, works_order="WO202", so_number="2202",
            material_group="component", class_id="COMP",
        )
        make_stock("FAB202", qty_on_hand=0)
        make_stock("COMP202", qty_on_hand=0)
        make_po("FAB202", outstanding_qty=10, po_num=202)
        make_po("COMP202", outstanding_qty=4, po_num=203)

        data = self.run_impact()

        assert all(not row["jobs_unlocked"] for row in data["single_results"])
        bundle = next(
            row for row in data["bundle_results"]
            if {po.key for po in row["pos"]} == {"202/1/1", "203/1/1"}
        )
        assert bundle["jobs_unlocked"] == ["WO202"]
        assert bundle["synergy_jobs"] == ["WO202"]

    def test_committed_po_stays_in_baseline_and_leaves_candidates(self, db):
        make_req("FAB203", qty_for_order=10, works_order="WO203", so_number="2203")
        make_req(
            "COMP203", qty_for_order=4, works_order="WO203", so_number="2203",
            material_group="component", class_id="COMP",
        )
        make_stock("FAB203", qty_on_hand=0)
        make_stock("COMP203", qty_on_hand=0)
        make_po("FAB203", outstanding_qty=10, po_num=204)
        make_po("COMP203", outstanding_qty=4, po_num=205)

        data = self.run_impact(committed_keys={"204/1/1"})

        assert [po.key for po in data["committed_pos"]] == ["204/1/1"]
        assert all(row["key"] != "204/1/1" for row in data["single_results"])
        component_result = next(
            row for row in data["single_results"] if row["key"] == "205/1/1"
        )
        assert component_result["jobs_unlocked"] == ["WO203"]

    def test_fabric_only_scope_ignores_component_blocker(self, db):
        make_req("FAB204", qty_for_order=10, works_order="WO204", so_number="2204")
        make_req(
            "COMP204", qty_for_order=4, works_order="WO204", so_number="2204",
            material_group="component", class_id="COMP",
        )
        make_stock("FAB204", qty_on_hand=0)
        make_stock("COMP204", qty_on_hand=0)
        make_po("FAB204", outstanding_qty=10, po_num=206)

        from app.purchasing.materials.services.release_impact import get_release_impact
        _db.session.commit()
        all_materials = get_release_impact(include_components=True)
        fabric_only = get_release_impact(include_components=False)

        all_result = next(
            row for row in all_materials["single_results"] if row["key"] == "206/1/1"
        )
        fabric_result = next(
            row for row in fabric_only["single_results"] if row["key"] == "206/1/1"
        )
        assert all_result["jobs_unlocked"] == []
        assert fabric_result["jobs_unlocked"] == ["WO204"]

    def test_release_impact_page_renders(self, client, planner_user):
        response = client.post("/auth/login", data={
            "login": planner_user.email,
            "password": "Planner!Pass1234",
        })
        assert response.status_code in (302, 303)

        response = client.get("/purchasing/materials/release-impact?q=FAB")

        assert response.status_code == 200
        assert b"Cash Release Impact" in response.data
        assert b"Bundled PO Release Impact" in response.data
        assert b"Fabric-only mode" in response.data

    def test_single_results_are_ranked_by_production_impact(self, db):
        make_req("FAB205", qty_for_order=10, works_order="WO205", so_number="2205")
        make_req("FAB206", qty_for_order=10, works_order="WO206", so_number="2206")
        make_req("FAB206", qty_for_order=10, works_order="WO207", so_number="2207")
        make_stock("FAB205", qty_on_hand=0)
        make_stock("FAB206", qty_on_hand=0)
        make_po("FAB205", outstanding_qty=10, po_num=207, unit_cost=1)
        make_po("FAB206", outstanding_qty=20, po_num=208, unit_cost=100)

        data = self.run_impact()

        assert data["single_results"][0]["key"] == "208/1/1"
        assert data["single_results"][0]["jobs_unlocked"] == ["WO206", "WO207"]

    def test_affected_jobs_excludes_job_already_covered_by_stock(self, db):
        make_req(
            "FAB207", qty_for_order=5, works_order="WO208", so_number="2208",
            due_date=TODAY + timedelta(days=10),
        )
        make_req(
            "FAB207", qty_for_order=10, works_order="WO209", so_number="2209",
            due_date=TODAY + timedelta(days=20),
        )
        make_stock("FAB207", qty_on_hand=5)
        make_po("FAB207", outstanding_qty=10, po_num=209)

        data = self.run_impact()

        result = next(row for row in data["single_results"] if row["key"] == "209/1/1")
        assert result["affected_jobs"] == 1
        assert result["jobs_unlocked"] == ["WO209"]
        assert "WO208" not in result["jobs_unlocked"]

    def test_cash_comparison_calculates_value_less_cash(self, db):
        from app.sales.orders.models import SalesOrder

        make_req("FAB208", qty_for_order=10, works_order="WO210", so_number="2210")
        make_stock("FAB208", qty_on_hand=0)
        make_po("FAB208", outstanding_qty=10, po_num=210, unit_cost=5)
        _db.session.add(SalesOrder(
            order_num=2210,
            order_line=1,
            rel_num=1,
            release_price_gbp=D(250),
        ))

        data = self.run_impact()

        result = next(row for row in data["single_results"] if row["key"] == "210/1/1")
        assert result["cash_proxy"] == D(50)
        assert result["order_value_unlocked"] == D(250)
        assert result["value_less_cash_proxy"] == D(200)
        assert result["return_ratio"] == D(5)

    def test_result_sort_options(self):
        from app.purchasing.materials.services.release_impact import (
            sort_release_impact_results,
        )

        low_cash = {
            "key": "1/1/1", "cash_proxy": D(10),
            "orders_unlocked": ["1"], "order_value_unlocked": D(100),
            "value_less_cash_proxy": D(90), "return_ratio": D(10),
            "jobs_unlocked": ["J1"], "jobs_improved": [],
            "result": "Releases production",
        }
        high_value = {
            "key": "2/1/1", "cash_proxy": D(100),
            "orders_unlocked": ["2", "3"], "order_value_unlocked": D(500),
            "value_less_cash_proxy": D(400), "return_ratio": D(5),
            "jobs_unlocked": ["J2"], "jobs_improved": [],
            "result": "Releases production",
        }
        rows = [high_value, low_cash]

        assert sort_release_impact_results(rows, "cash_proxy")[0] is low_cash
        assert sort_release_impact_results(rows, "orders_unlocked")[0] is high_value
        assert sort_release_impact_results(rows, "order_value_unlocked")[0] is high_value
        assert sort_release_impact_results(rows, "value_less_cash")[0] is high_value
        assert sort_release_impact_results(rows, "value_cash")[0] is low_cash

    def test_supplier_result_recalculates_combined_po_impact(self, db):
        make_req("FAB209", qty_for_order=10, works_order="WO211", so_number="2211")
        make_req("FAB210", qty_for_order=10, works_order="WO211", so_number="2211")
        make_stock("FAB209", qty_on_hand=0)
        make_stock("FAB210", qty_on_hand=0)
        make_po(
            "FAB209", outstanding_qty=10, po_num=211,
            supplier_name="Combined Supplier",
        )
        make_po(
            "FAB210", outstanding_qty=10, po_num=212,
            supplier_name="Combined Supplier",
        )

        data = self.run_impact()

        supplier = next(
            row for row in data["supplier_results"]
            if row["supplier"] == "Combined Supplier"
        )
        assert supplier["po_count"] == 2
        assert supplier["jobs_unlocked"] == ["WO211"]
        assert all(
            "WO211" not in row["jobs_unlocked"]
            for row in data["single_results"]
        )

    def test_staged_selection_returns_combined_decision_summary(self, db):
        make_req("FAB211", qty_for_order=10, works_order="WO212", so_number="2212")
        make_req("FAB212", qty_for_order=10, works_order="WO212", so_number="2212")
        make_stock("FAB211", qty_on_hand=0)
        make_stock("FAB212", qty_on_hand=0)
        make_po("FAB211", outstanding_qty=10, po_num=213, unit_cost=2)
        make_po("FAB212", outstanding_qty=10, po_num=214, unit_cost=3)

        from app.purchasing.materials.services.release_impact import get_release_impact
        _db.session.commit()
        data = get_release_impact(staged_keys={"213/1/1", "214/1/1"})

        assert data["staged_keys"] == ["213/1/1", "214/1/1"]
        assert data["staged_result"]["cash_proxy"] == D(50)
        assert data["staged_result"]["jobs_unlocked"] == ["WO212"]
        assert data["staged_result"]["supplier_groups"] == [{
            "supplier": "Test Supplier",
            "pos": data["staged_result"]["pos"],
            "po_count": 2,
            "cash_proxy": D(50),
            "outstanding_qty": D(20),
        }]

    def test_staged_selection_groups_pos_by_supplier(self, db):
        make_req("FAB214", qty_for_order=5, works_order="WO214", so_number="2214")
        make_req("FAB215", qty_for_order=5, works_order="WO215", so_number="2215")
        make_stock("FAB214", qty_on_hand=0)
        make_stock("FAB215", qty_on_hand=0)
        make_po(
            "FAB214", outstanding_qty=5, po_num=216, unit_cost=2,
            supplier_name="Alpha Supplier",
        )
        make_po(
            "FAB215", outstanding_qty=5, po_num=217, unit_cost=3,
            supplier_name="Beta Supplier",
        )

        from app.purchasing.materials.services.release_impact import get_release_impact
        _db.session.commit()
        data = get_release_impact(staged_keys={"216/1/1", "217/1/1"})

        groups = data["staged_result"]["supplier_groups"]
        assert [group["supplier"] for group in groups] == [
            "Alpha Supplier", "Beta Supplier",
        ]
        assert [group["cash_proxy"] for group in groups] == [D(10), D(15)]

    def test_po_details_include_name_quantities_uom_and_due_date(self, db):
        due = TODAY + timedelta(days=7)
        make_req("FAB213", qty_for_order=8, works_order="WO213", so_number="2213")
        make_stock("FAB213", qty_on_hand=0)
        make_po(
            "FAB213", outstanding_qty=8, po_num=215, due_date=due,
            line_desc="Blue Herringbone", unit_of_measure="M",
            rel_qty=12, received_qty=4,
        )

        data = self.run_impact()

        po = next(
            row["pos"][0]
            for row in data["single_results"]
            if row["key"] == "215/1/1"
        )
        assert po.description == "Blue Herringbone"
        assert po.unit_of_measure == "M"
        assert po.release_qty == D(12)
        assert po.received_qty == D(4)
        assert po.outstanding_qty == D(8)
        assert po.due_date == due


# ---------------------------------------------------------------------------
# Durable persistence: staged/committed release decisions across sync
# ---------------------------------------------------------------------------

class TestReleaseDecisions:
    @pytest.fixture(autouse=True)
    def ctx(self, app, zero_lead_days):
        with app.test_request_context():
            yield

    def test_stage_selection_persists_and_can_be_read_back(self, db):
        from app.purchasing.materials.services.release_decisions import (
            get_active_staged_keys,
            get_staged_snapshot,
            stage_selection,
        )
        from app.purchasing.materials.services.release_impact import get_release_impact

        make_req("FAB301", qty_for_order=10, works_order="WO301", so_number="3301")
        make_stock("FAB301", qty_on_hand=0)
        make_po("FAB301", outstanding_qty=10, po_num=301, unit_cost=5)
        _db.session.commit()

        data = get_release_impact(staged_keys={"301/1/1"})
        stage_selection(data["staged_result"]["pos"], data["staged_result"])

        assert get_active_staged_keys() == {"301/1/1"}
        snapshot = get_staged_snapshot()
        assert snapshot["cash_proxy"] == D(50)
        assert snapshot["jobs_unlocked"] == 1

    def test_restaging_replaces_previous_selection(self, db):
        from app.purchasing.materials.services.release_decisions import (
            get_active_staged_keys,
            stage_selection,
        )
        from app.purchasing.materials.services.release_impact import get_release_impact

        make_req("FAB302", qty_for_order=10, works_order="WO302", so_number="3302")
        make_req("FAB303", qty_for_order=10, works_order="WO303", so_number="3303")
        make_stock("FAB302", qty_on_hand=0)
        make_stock("FAB303", qty_on_hand=0)
        make_po("FAB302", outstanding_qty=10, po_num=302)
        make_po("FAB303", outstanding_qty=10, po_num=303)
        _db.session.commit()

        first = get_release_impact(staged_keys={"302/1/1"})
        stage_selection(first["staged_result"]["pos"], first["staged_result"])
        assert get_active_staged_keys() == {"302/1/1"}

        second = get_release_impact(staged_keys={"303/1/1"})
        stage_selection(second["staged_result"]["pos"], second["staged_result"])

        assert get_active_staged_keys() == {"303/1/1"}

    def test_commit_staged_moves_status_and_clears_staged(self, db):
        from app.purchasing.materials.services.release_decisions import (
            commit_staged,
            get_active_committed_keys,
            get_active_staged_keys,
            stage_selection,
        )
        from app.purchasing.materials.services.release_impact import get_release_impact

        make_req("FAB304", qty_for_order=10, works_order="WO304", so_number="3304")
        make_stock("FAB304", qty_on_hand=0)
        make_po("FAB304", outstanding_qty=10, po_num=304)
        _db.session.commit()

        data = get_release_impact(staged_keys={"304/1/1"})
        stage_selection(data["staged_result"]["pos"], data["staged_result"])

        committed = commit_staged()

        assert committed == 1
        assert get_active_staged_keys() == set()
        assert get_active_committed_keys() == {"304/1/1"}

    def test_clear_staged_withdraws_without_committing(self, db):
        from app.purchasing.materials.services.release_decisions import (
            clear_staged,
            get_active_committed_keys,
            get_active_staged_keys,
            stage_selection,
        )
        from app.purchasing.materials.services.release_impact import get_release_impact

        make_req("FAB305", qty_for_order=10, works_order="WO305", so_number="3305")
        make_stock("FAB305", qty_on_hand=0)
        make_po("FAB305", outstanding_qty=10, po_num=305)
        _db.session.commit()

        data = get_release_impact(staged_keys={"305/1/1"})
        stage_selection(data["staged_result"]["pos"], data["staged_result"])

        cleared = clear_staged()

        assert cleared == 1
        assert get_active_staged_keys() == set()
        assert get_active_committed_keys() == set()

    def test_update_committed_selection_withdraws_unticked_pos(self, db):
        from app.purchasing.materials.services.release_decisions import (
            commit_staged,
            get_active_committed_keys,
            stage_selection,
            update_committed_selection,
        )
        from app.purchasing.materials.services.release_impact import get_release_impact

        make_req("FAB306", qty_for_order=10, works_order="WO306", so_number="3306")
        make_req("FAB307", qty_for_order=10, works_order="WO307", so_number="3307")
        make_stock("FAB306", qty_on_hand=0)
        make_stock("FAB307", qty_on_hand=0)
        make_po("FAB306", outstanding_qty=10, po_num=306)
        make_po("FAB307", outstanding_qty=10, po_num=307)
        _db.session.commit()

        data = get_release_impact(staged_keys={"306/1/1", "307/1/1"})
        stage_selection(data["staged_result"]["pos"], data["staged_result"])
        commit_staged()
        assert get_active_committed_keys() == {"306/1/1", "307/1/1"}

        removed = update_committed_selection({"306/1/1"})

        assert removed == 1
        assert get_active_committed_keys() == {"306/1/1"}

    def test_reconcile_drops_committed_po_once_fully_received(self, db):
        from app.purchasing.materials.models import ReleaseDecision
        from app.purchasing.materials.services.release_decisions import (
            get_active_committed_keys,
            reconcile_release_decisions,
        )

        po = make_po("FAB308", outstanding_qty=10, po_num=308)
        _db.session.add(ReleaseDecision(
            po_num=308, po_line=1, po_release=1,
            status=ReleaseDecision.STATUS_COMMITTED,
        ))
        _db.session.commit()
        assert get_active_committed_keys() == {"308/1/1"}

        # Simulate the next sync: the PO has now been fully received.
        po.outstanding_qty = D(0)
        _db.session.commit()

        summary = reconcile_release_decisions()

        assert summary == {"checked": 1, "fulfilled": 1}
        assert get_active_committed_keys() == set()
        decision = ReleaseDecision.query.filter_by(po_num=308).one()
        assert decision.status == ReleaseDecision.STATUS_FULFILLED
        assert decision.closed_reason == "received"

    def test_reconcile_drops_committed_po_removed_from_sync(self, db):
        from app.purchasing.materials.models import ReleaseDecision
        from app.purchasing.materials.services.release_decisions import (
            get_active_committed_keys,
            reconcile_release_decisions,
        )

        make_po("FAB309", outstanding_qty=10, po_num=309)
        _db.session.add(ReleaseDecision(
            po_num=309, po_line=1, po_release=1,
            status=ReleaseDecision.STATUS_COMMITTED,
        ))
        _db.session.commit()

        # Simulate a full truncate+reload sync where this PO is no longer
        # in the open-PO set at all (i.e. it has been fully received).
        PurchaseOrder.query.filter_by(po_num=309).delete()
        _db.session.commit()

        reconcile_release_decisions()

        assert get_active_committed_keys() == set()

    def test_reconcile_leaves_still_open_decisions_untouched(self, db):
        from app.purchasing.materials.models import ReleaseDecision
        from app.purchasing.materials.services.release_decisions import (
            get_active_committed_keys,
            reconcile_release_decisions,
        )

        make_po("FAB310", outstanding_qty=10, po_num=310)
        _db.session.add(ReleaseDecision(
            po_num=310, po_line=1, po_release=1,
            status=ReleaseDecision.STATUS_COMMITTED,
        ))
        _db.session.commit()

        summary = reconcile_release_decisions()

        assert summary == {"checked": 1, "fulfilled": 0}
        assert get_active_committed_keys() == {"310/1/1"}

    def test_stage_commit_clear_routes_persist_across_requests(self, db, client, admin_user):
        make_req("FAB311", qty_for_order=10, works_order="WO311", so_number="3311")
        make_stock("FAB311", qty_on_hand=0)
        make_po("FAB311", outstanding_qty=10, po_num=311, unit_cost=5)
        _db.session.commit()

        client.post("/auth/login", data={
            "login": admin_user.email,
            "password": "Admin!Pass1234",
        })

        stage_response = client.post(
            "/purchasing/materials/release-impact/stage",
            data={"staged": "311/1/1", "scope": "fabric", "sort": "impact"},
        )
        assert stage_response.status_code in (302, 303)

        # A fresh request (no query params) should still see the staged PO —
        # proving the decision lives in the database, not the URL.
        page = client.get("/purchasing/materials/release-impact")
        assert b"Staged Release Decision" in page.data
        assert b"311/1/1" in page.data

        commit_response = client.post(
            "/purchasing/materials/release-impact/commit",
            data={"scope": "fabric", "sort": "impact"},
        )
        assert commit_response.status_code in (302, 303)

        from app.purchasing.materials.services.release_decisions import (
            get_active_committed_keys,
            get_active_staged_keys,
        )
        assert get_active_staged_keys() == set()
        assert get_active_committed_keys() == {"311/1/1"}

    def test_staged_drift_flagged_after_underlying_data_changes(self, db, client, admin_user):
        from app.purchasing.materials.services.release_decisions import stage_selection
        from app.purchasing.materials.services.release_impact import get_release_impact

        make_req("FAB312", qty_for_order=10, works_order="WO312", so_number="3312")
        make_stock("FAB312", qty_on_hand=0)
        po = make_po("FAB312", outstanding_qty=10, po_num=312, unit_cost=5)
        _db.session.commit()

        data = get_release_impact(staged_keys={"312/1/1"})
        stage_selection(data["staged_result"]["pos"], data["staged_result"])

        client.post("/auth/login", data={
            "login": admin_user.email,
            "password": "Admin!Pass1234",
        })

        # A day-one page load, before any sync change, shows no drift.
        page = client.get("/purchasing/materials/release-impact")
        assert b"Changed since staged" not in page.data

        # Simulate a sync changing the PO's cost (its cash proxy).
        po.unit_cost = D(9)
        _db.session.commit()

        page = client.get("/purchasing/materials/release-impact")
        assert b"Changed since staged" in page.data

    def test_staged_drift_flagged_when_identities_change_but_counts_match(self, db):
        """
        A sync can leave the headline counts unchanged while swapping which
        specific job/order the release actually unlocks (for example, one
        job's requirement is fully issued and drops out between staging and
        the next review). Counts alone would miss this; drift detection must
        also compare the job/order identities recorded in the snapshot.
        """
        from app.purchasing.materials.services.release_decisions import (
            get_staged_snapshot,
            stage_selection,
        )
        from app.purchasing.materials.services.release_impact import get_release_impact

        same_due = TODAY + timedelta(days=30)
        make_req(
            "FABSWAP", qty_for_order=5, works_order="WOA1", so_number="SOA1",
            due_date=same_due,
        )
        make_req(
            "FABSWAP", qty_for_order=5, works_order="WOA2", so_number="SOA2",
            due_date=same_due,
        )
        make_stock("FABSWAP", qty_on_hand=0)
        make_po("FABSWAP", outstanding_qty=5, po_num=401, unit_cost=1)
        _db.session.commit()

        data = get_release_impact(staged_keys={"401/1/1"})
        staged_result = data["staged_result"]
        # WOA1 sorts first (due date tie, works_order tiebreak) and consumes
        # the whole 5-unit release; WOA2 stays blocked.
        assert staged_result["jobs_unlocked"] == ["WOA1"]
        assert staged_result["orders_unlocked"] == ["SOA1"]
        stage_selection(staged_result["pos"], staged_result)

        snapshot = get_staged_snapshot()
        assert snapshot["jobs_unlocked"] == 1
        assert snapshot["jobs_unlocked_keys"] == ["WOA1"]
        assert snapshot["orders_unlocked_keys"] == ["SOA1"]

        # Simulate a sync: WOA1's requirement is fully issued/closed and
        # drops out of the feed, leaving only WOA2 - which the same release
        # now fully covers instead. The unlocked count is still 1/1.
        MaterialRequirementMain.query.filter_by(works_order="WOA1").delete()
        _db.session.commit()

        new_data = get_release_impact(staged_keys={"401/1/1"})
        new_staged_result = new_data["staged_result"]
        assert new_staged_result["jobs_unlocked"] == ["WOA2"]
        assert new_staged_result["orders_unlocked"] == ["SOA2"]
        assert len(new_staged_result["jobs_unlocked"]) == snapshot["jobs_unlocked"]
        assert len(new_staged_result["orders_unlocked"]) == snapshot["orders_unlocked"]

        # Counts match, but the identities recorded in the snapshot don't -
        # this is exactly what the route-level drift check must catch.
        current_jobs = set(new_staged_result["jobs_unlocked"])
        previous_jobs = set(snapshot["jobs_unlocked_keys"])
        assert current_jobs != previous_jobs

    def test_committed_component_po_stays_visible_and_manageable_in_fabric_scope(self, db):
        """
        A component PO committed while viewing "fabric and components" must
        still be listed (and stay manageable) when later viewing the
        fabric-only default scope - not silently disappear, and not be
        dropped from the committed baseline just because the fabric-only
        view never rendered a checkbox for it.
        """
        from app.purchasing.materials.services.release_decisions import (
            commit_staged,
            get_active_committed_keys,
            stage_selection,
            update_committed_selection,
        )
        from app.purchasing.materials.services.release_impact import get_release_impact

        make_req("FAB410", qty_for_order=10, works_order="WO410", so_number="4410")
        make_req(
            "COMP410", qty_for_order=10, works_order="WO411", so_number="4411",
            material_group="component",
        )
        make_stock("FAB410", qty_on_hand=0)
        make_stock("COMP410", qty_on_hand=0)
        make_po("FAB410", outstanding_qty=10, po_num=410, unit_cost=5)
        make_po("COMP410", outstanding_qty=10, po_num=411, unit_cost=3)
        _db.session.commit()

        all_scope = get_release_impact(
            staged_keys={"410/1/1", "411/1/1"}, include_components=True
        )
        stage_selection(all_scope["staged_result"]["pos"], all_scope["staged_result"])
        commit_staged()
        assert get_active_committed_keys() == {"410/1/1", "411/1/1"}

        # Fabric-only (the default scope) still shows the component PO in
        # the committed baseline, flagged as out of scope, and keeps its
        # value out of the in-scope committed total.
        fabric_scope = get_release_impact(
            committed_keys=get_active_committed_keys(), include_components=False
        )
        committed_by_key = {po.key: po for po in fabric_scope["committed_pos"]}
        assert set(committed_by_key) == {"410/1/1", "411/1/1"}
        assert committed_by_key["410/1/1"].in_scope is True
        assert committed_by_key["411/1/1"].in_scope is False
        assert fabric_scope["committed_out_of_scope_count"] == 1
        assert fabric_scope["committed_value"] == D(50)  # FAB410 only

        # Submitting "update committed baseline" from this fabric-only view
        # with every rendered checkbox still ticked (the template default)
        # must not silently drop the out-of-scope component PO.
        rendered_keys = set(committed_by_key)
        removed = update_committed_selection(rendered_keys)
        assert removed == 0
        assert get_active_committed_keys() == {"410/1/1", "411/1/1"}

