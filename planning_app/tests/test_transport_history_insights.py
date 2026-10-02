"""Filtered history insights count identities, not shipped units or joined rows."""

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.extensions import db
from app.transport.history_insights import build_history_insights
from app.transport.manifest_history import get_manifest_history
from app.transport.models import TransportManifest, TransportManifestRelease
from tests.test_transport_loads import login_admin


@pytest.fixture(autouse=True)
def ctx(app):
    with app.app_context():
        yield


def saved(load_id, orders=(), ship_date=date(2026, 9, 1), route="North",
          status="SHIPPED", customer="Example customer"):
    now = datetime.now(timezone.utc)
    manifest = TransportManifest(
        load_id=load_id, route=route, source_status=status, ship_date=ship_date,
        first_observed_at=now, last_seen_at=now, contents_observed_at=now,
    )
    for line, order in enumerate(orders, 1):
        manifest.releases.append(TransportManifestRelease(
            order_num=order, order_line=line, rel_num=1, customer=customer,
            part_num="SOFA", jobs=["JOB-1", "JOB-2"],
            packs=[{"number": 1, "line": 1}, {"number": 2, "line": 1}],
        ))
    db.session.add(manifest)
    db.session.commit()
    return manifest


def insights():
    return build_history_insights(TransportManifest.query, today=date(2026, 10, 2))


def test_empty_insights_are_explicit_not_an_average_of_zero():
    result = insights()
    assert result["count"] == result["orders"] == result["releases"] == 0
    assert result["orders_per_load"] is None
    assert result["multi_load_orders"] == 0
    assert result["monthly"] == result["routes"] == []
    assert all(row["count"] == row["width"] == 0 for row in result["distribution"])


def test_deduplicates_order_appearances_and_keeps_releases_distinct():
    saved("ONE", [100, 100, 200])
    saved("TWO", [100])
    saved("EMPTY")
    result = insights()
    assert result["count"] == 3
    assert result["orders"] == 2
    assert result["releases"] == 4
    assert result["orders_per_load"] == 1.0  # Three load/order pairs, not four releases.
    assert result["multi_load_orders"] == 1
    counts = {row["label"]: row["count"] for row in result["distribution"]}
    assert counts == {"No orders": 1, "1 order": 1, "2-5 orders": 1,
                      "6-10 orders": 0, "11-20 orders": 0, "21+ orders": 0}
    assert sum(counts.values()) == result["count"]
    assert result["monthly"][0]["count"] == 3


def test_full_filter_scope_not_page_and_search_selects_complete_load():
    for number in range(25):
        saved(f"LOAD-{number:02}", [1000 + number, 2000 + number])
    page_one = get_manifest_history(page=1)
    page_two = get_manifest_history(page=2)
    assert len(page_one["manifests"]) == 20
    assert len(page_two["manifests"]) == 5
    assert page_one["insights"] == page_two["insights"]
    assert page_one["insights"]["count"] == 25
    assert page_one["insights"]["orders"] == 50
    filtered = get_manifest_history(search="1003")["insights"]
    assert filtered["count"] == 1
    assert filtered["orders"] == filtered["releases"] == 2
    assert filtered["orders_per_load"] == 2.0
    assert get_manifest_history(route="South")["insights"]["count"] == 0
    assert get_manifest_history(ship_from=date(2026, 10, 1))["insights"]["count"] == 0
    assert get_manifest_history(ship_to=date(2026, 8, 31))["insights"]["count"] == 0
    assert get_manifest_history(search="Example customer")["insights"]["count"] == 25


def test_monthly_chart_has_zero_months_bounds_and_explicit_date_exceptions():
    saved("OLD", [1], ship_date=date(2024, 1, 1))
    saved("RECENT", [2], ship_date=date(2026, 8, 1))
    saved("FUTURE", [3], ship_date=date(2026, 10, 31))
    saved("UNDATED", [4], ship_date=None, status="SCHEDULED")
    result = insights()
    assert len(result["monthly"]) == 12
    assert result["monthly"][0]["label"] == "Nov 2025"
    assert result["monthly"][-1]["label"] == "Oct 2026"
    assert next(row for row in result["monthly"] if row["label"] == "Sep 2026")["count"] == 0
    assert sum(row["count"] for row in result["monthly"]) == 2
    assert result["undated"] == result["future"] == result["status_changed"] == 1
    assert result["count"] == 4


def test_all_undated_history_still_has_route_and_consolidation_charts():
    saved("UNDATED", [1], ship_date=None, route=None)
    result = insights()
    assert result["monthly"] == []
    assert result["routes"][0]["label"] == "No route"
    assert result["undated"] == result["count"] == 1


def test_blank_and_null_routes_share_one_no_route_group():
    saved("NULL", [1], route=None)
    saved("BLANK", [2], route="")
    result = insights()
    assert result["routes"] == [
        {"label": "No route", "route": None, "count": 2, "share": 100.0, "width": 100.0},
    ]


def test_route_mix_is_top_five_plus_other_with_reconciled_counts():
    for index in range(7):
        saved(str(index), [index + 1], route=f"Route-{index}")
    result = insights()
    assert len(result["routes"]) == 6
    assert result["routes"][-1]["label"] == "Other routes"
    assert result["routes"][-1]["route"] is None
    assert result["routes"][-1]["count"] == 2
    assert sum(row["count"] for row in result["routes"]) == 7
    assert all(0 <= row["width"] <= 100 for row in result["routes"])
    assert result["routes"][-1]["width"] == 100


@pytest.mark.parametrize("orders,label", [
    (0, "No orders"), (1, "1 order"), (2, "2-5 orders"), (5, "2-5 orders"),
    (6, "6-10 orders"), (10, "6-10 orders"), (11, "11-20 orders"),
    (20, "11-20 orders"), (21, "21+ orders"),
])
def test_order_distribution_boundaries(orders, label):
    saved("LOAD", range(1, orders + 1))
    result = insights()
    assert next(row for row in result["distribution"] if row["label"] == label)["count"] == 1
    assert sum(row["count"] for row in result["distribution"]) == 1


def test_insights_render_labels_route_drillthrough_and_escape(client, admin_user):
    saved("LOAD", [1, 2], route="<b>North</b>", status="SCHEDULED",
          ship_date=date(2026, 10, 31))
    saved("UNDATED", [3], ship_date=None)
    login_admin(client)
    with patch("app.transport.history_insights.date") as mocked_date:
        mocked_date.today.return_value = date(2026, 10, 2)
        mocked_date.side_effect = date
        html = client.get("/transport/manifest-history?q=LOAD").get_data(as_text=True)
    assert "History insights" in html
    assert "not just this page" in html
    assert "Average orders / load" in html
    assert "not shipped units or utilisation" in html
    assert "1 future-dated load included" in html
    assert "1 saved load now has a non-shipped source status" in html
    assert "&lt;b&gt;North&lt;/b&gt;" in html
    assert "route=%3Cb%3ENorth%3C/b%3E" in html
    assert "q=LOAD" in html
