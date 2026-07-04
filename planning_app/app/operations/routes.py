"""Operations department portal routes."""

from datetime import date

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import func

from . import operations_bp
from .models import WorksOrder
from app.extensions import db


@operations_bp.route("/")
@operations_bp.route("/dashboard")
@login_required
def dashboard():
    total     = db.session.query(func.count(WorksOrder.id)).scalar() or 0
    released  = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.job_released == True).scalar() or 0
    firm      = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.job_firm == True).scalar() or 0
    shortages = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.mtl_shortage == True).scalar() or 0
    waiting   = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.waiting_temp == True).scalar() or 0

    # Latest sync timestamp
    last = WorksOrder.query.order_by(WorksOrder.imported_at.desc()).first()

    # Paginated job list (main assemblies only: assembly_seq = 0)
    search   = request.args.get("q", "").strip()
    page     = request.args.get("page", 1, type=int)
    q = WorksOrder.query.filter(WorksOrder.assembly_seq == 0)
    if search:
        term = f"%{search}%"
        q = q.filter(
            db.or_(
                WorksOrder.job_num.ilike(term),
                WorksOrder.customer_name.ilike(term),
                WorksOrder.description.ilike(term),
                WorksOrder.model.ilike(term),
            )
        )
    jobs = q.order_by(WorksOrder.prod_plnwk, WorksOrder.req_due_date).paginate(
        page=page, per_page=50, error_out=False
    )

    return render_template(
        "operations/dashboard.html",
        title="Operations",
        total=total,
        released=released,
        firm=firm,
        shortages=shortages,
        waiting=waiting,
        jobs=jobs,
        search=search,
        last_imported=last.imported_at if last else None,
        today=date.today(),
    )

