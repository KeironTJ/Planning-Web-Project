"""Tests for Operations WIP ordering."""

from datetime import date

from app.extensions import db
from app.operations.models import WorksOrder
from app.operations.routes import _wip_job_ordering


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
