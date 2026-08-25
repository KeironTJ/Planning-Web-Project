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


def make_stock(part_num="FAB001", qty_on_hand=0, plant="STORES"):
    s = Stock(part_num=part_num, qty_on_hand=D(qty_on_hand), plant=plant)
    _db.session.add(s)
    return s


def make_po(part_num="FAB001", outstanding_qty=0, due_date=None,
            po_num=1, po_line=1, po_release=1):
    po = PurchaseOrder(
        po_num=po_num, po_line=po_line, po_release=po_release,
        part_num=part_num,
        outstanding_qty=D(outstanding_qty),
        due_date=due_date or (TODAY + timedelta(days=5)),
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
