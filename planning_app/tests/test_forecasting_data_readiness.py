from datetime import date, datetime, timezone
from decimal import Decimal

from app.forecasting.services import get_intake_data_readiness
from app.sales.orders.models import SalesOrder

from .conftest import login


def test_data_readiness_reports_local_order_quality_without_claiming_history(
    client, planner_user, db_session
):
    imported_at = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    db_session.add_all(
        [
            SalesOrder(
                order_num=12,
                order_line=1,
                rel_num=1,
                order_date=date(2026, 1, 1),
                order_received_date=date(2026, 1, 2),
                req_date=date(2026, 2, 1),
                release_qty=Decimal("3"),
                release_price_gbp=Decimal("120"),
                ium="EA",
                currency_code="GBP",
                imported_at=imported_at,
            ),
            SalesOrder(
                order_num=12,
                order_line=1,
                rel_num=1,
                order_date=date(2026, 1, 1),
                order_received_date=date(2026, 1, 2),
                req_date=date(2026, 2, 1),
                release_qty=Decimal("3"),
                release_price_gbp=Decimal("120"),
                ium="EA",
                currency_code="GBP",
                imported_at=imported_at,
            ),
            SalesOrder(
                order_num=13,
                order_line=1,
                rel_num=1,
                order_date=date(2026, 1, 20),
                req_date=date(2026, 1, 19),
                release_qty=Decimal("0"),
                ium="M",
                currency_code="EUR",
            ),
        ]
    )
    db_session.commit()
    readiness = get_intake_data_readiness()
    assert readiness.row_count == 3
    assert readiness.order_count == 2
    assert readiness.release_count == 2
    assert readiness.repeated_release_count == 1
    assert readiness.missing_received_date_count == 1
    assert readiness.ship_by_before_order_count == 1
    assert readiness.nonpositive_release_qty_count == 1
    assert readiness.missing_or_nonpositive_gbp_value_count == 1
    assert readiness.quantity_units == ("EA", "M")
    assert readiness.currencies == ("EUR", "GBP")
    login(client, planner_user.email, "Planner!Pass1234")

    response = client.get("/ai-tools/data-readiness")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Not ready for training." in html
    assert "mutable local sales-order table" in html
    assert '<div class="fs-4 fw-semibold">3</div>' in html
    assert "Required dates earlier than order dates" in html
    assert ">EA, M</td>" in html
    assert ">EUR, GBP</td>" in html
    assert "actual shipment detail" in html


def test_data_readiness_requires_planning_view_permission(client):
    assert client.get("/ai-tools/data-readiness").status_code == 302
