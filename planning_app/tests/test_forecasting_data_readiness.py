from datetime import date, datetime, timezone
from decimal import Decimal

from app.forecasting.services import get_intake_data_readiness
from app.forecasting.weekly_intake import END, FIRST_WEEK, WEEK
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
    assert "Choose the data you want to explore" in html
    assert "No saved extracts yet" in html
    assert "mutable local sales-order table" in html
    assert '<div class="fs-4 fw-semibold">3</div>' in html
    assert "Required dates earlier than order dates" in html
    assert ">EA, M</td>" in html
    assert ">EUR, GBP</td>" in html
    assert "actual shipment detail" in html
    assert "01/01/2026 to 20/01/2026" in html
    assert "02/01/2026 to 02/01/2026" in html
    assert "19/01/2026 to 01/02/2026" in html
    assert 'data-fmt="uk-datetime">12/11/2026 12:00:00</time>' in html


def test_readiness_uses_consistent_release_order_dates_not_received_dates(
    client, planner_user, db_session,
):
    observations = {
        1: [(0, FIRST_WEEK), (0, FIRST_WEEK), (1, END)],
        2: [(None, None), (1, FIRST_WEEK)],
        3: [(0, FIRST_WEEK), (0, FIRST_WEEK + WEEK)],
        4: [(0, None), (0, FIRST_WEEK)],
        5: [(0, date(2025, 12, 31))],
        6: [(0, END)],
        7: [(1, date(2027, 1, 3))],
    }
    db_session.add_all(
        SalesOrder(
            order_num=order, order_line=1, rel_num=1,
            assembly_seq=assembly, order_date=ordered,
            order_received_date=None if order == 1 else FIRST_WEEK,
        )
        for order, rows in observations.items()
        for assembly, ordered in rows
    )
    db_session.commit()

    readiness = get_intake_data_readiness()
    assert readiness.release_count == 7
    assert readiness.missing_order_date_release_count == 1
    assert readiness.inconsistent_order_date_release_count == 2
    assert readiness.in_calendar_order_date_release_count == 2
    assert readiness.outside_calendar_order_date_release_count == 2
    assert (readiness.order_date_min, readiness.order_date_max) == (date(2025, 12, 31), END)
    assert readiness.missing_received_date_count == 3

    login(client, planner_user.email, "Planner!Pass1234")
    response = client.get("/ai-tools/data-readiness")
    assert response.status_code == 200
    html = " ".join(response.get_data(as_text=True).split())
    assert html.index("Project 1: Epicor order-date readiness") < html.index("Missing order-received dates")
    assert "Releases with missing order dates</th> <td>1</td>" in html
    assert "Releases with inconsistent order dates</th> <td>2</td>" in html
    assert "Releases dated outside SOI 2026</th> <td>2</td>" in html
    assert "05/01/2026 to 03/01/2027, inclusive" in html


def test_data_readiness_requires_planning_view_permission(client):
    assert client.get("/ai-tools/data-readiness").status_code == 302
