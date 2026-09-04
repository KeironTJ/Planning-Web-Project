"""Tests for Operations WIP ordering."""

from datetime import date
from types import SimpleNamespace

from app.extensions import db
from app.operations.models import WorksOrder
from app.operations.routes import _quick_win_jobs, _wip_job_ordering


def test_wip_jobs_are_ordered_by_due_date_sequence_order_and_job(app):
    with app.app_context():
        common = {"assembly_seq": 0, "job_released": True, "job_complete": False}
        db.session.add_all([
            WorksOrder(
                job_num="B-20", order_num=200, order_sort=20, next_op="UPH",
                req_due_date=date(2026, 9, 14), **common
            ),
            WorksOrder(
                job_num="B-10", order_num=200, order_sort=10, next_op="FRM",
                req_due_date=date(2026, 9, 14), **common
            ),
            WorksOrder(
                job_num="A-20", order_num=100, order_sort=20, next_op="UPH",
                req_due_date=date(2026, 9, 14), **common
            ),
            WorksOrder(
                job_num="A-10", order_num=100, order_sort=10, next_op="FRM",
                req_due_date=date(2026, 9, 14), **common
            ),
            WorksOrder(
                job_num="C-10", order_num=300, order_sort=30, next_op="FRM",
                req_due_date=date(2026, 9, 7), **common
            ),
        ])
        db.session.commit()

        jobs = WorksOrder.query.order_by(*_wip_job_ordering()).all()

        assert [(job.order_num, job.job_num) for job in jobs] == [
            (300, "C-10"),
            (100, "A-10"),
            (200, "B-10"),
            (100, "A-20"),
            (200, "B-20"),
        ]


def test_quick_wins_require_uphol_as_earliest_operation_and_no_shortage():
    completed = SimpleNamespace(order_num=100, job_complete=True, next_op=None)
    uphol = SimpleNamespace(order_num=100, job_complete=False, next_op="UPHOL")
    later = SimpleNamespace(order_num=100, job_complete=False, next_op="SEW")
    finished = SimpleNamespace(order_num=100, job_complete=False, next_op="FINISH")
    single = SimpleNamespace(order_num=400, job_complete=False, next_op="UPHOL")
    beyond = SimpleNamespace(order_num=500, job_complete=False, next_op="FINISH")
    earlier = SimpleNamespace(order_num=200, job_complete=False, next_op="FRAME")
    blocked = SimpleNamespace(order_num=300, job_complete=False, next_op="UPHOL")

    jobs = _quick_win_jobs(
        [completed, uphol, later, finished, single, beyond, earlier, blocked],
        {100, 200, 300},
        {400, 500},
        {"100": "ok", "200": "no_data", "300": "high_risk"},
        {"100": "no_data", "200": "no_data", "300": "ok", "400": "ok", "500": "ok"},
        {"FRAME": 1, "UPHOL": 2, "SEW": 3, "FINISH": 4},
    )

    assert jobs == [uphol, later, finished, single, beyond]
