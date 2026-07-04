'''Operations department portal routes.'''

from collections import defaultdict
from datetime import date, timedelta
from flask import render_template, request
from flask_login import login_required
from sqlalchemy import func
from . import operations_bp
from .models import WorksOrder, ProductionOutput
from app.extensions import db
from app.sales.orders.models import Department as DeptModel
from app.admin.models import SystemSetting, SETTING_DAILY_OUTPUT_TARGET, SETTING_DAILY_OUTPUT_TARGET_DAYS

@operations_bp.route('/')
@operations_bp.route('/dashboard')
@login_required
def dashboard():
    total     = db.session.query(func.count(WorksOrder.id)).scalar() or 0
    released  = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.job_released == True).scalar() or 0
    shortages = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.mtl_shortage == True).scalar() or 0
    waiting   = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.waiting_temp == True).scalar() or 0
    last = WorksOrder.query.order_by(WorksOrder.imported_at.desc()).first()
    search = request.args.get('q', '').strip()
    page   = request.args.get('page', 1, type=int)
    q = WorksOrder.query.filter(WorksOrder.assembly_seq == 0)
    if search:
        term = f'%{search}%'
        q = q.filter(db.or_(WorksOrder.job_num.ilike(term), WorksOrder.customer_name.ilike(term), WorksOrder.description.ilike(term), WorksOrder.model.ilike(term)))
    jobs = q.order_by(WorksOrder.prod_plnwk, WorksOrder.req_due_date).paginate(page=page, per_page=50, error_out=False)
    return render_template('operations/dashboard.html', title='Operations', total=total, released=released, shortages=shortages, waiting=waiting, jobs=jobs, search=search, last_imported=last.imported_at if last else None, today=date.today())

@operations_bp.route('/daily-output')
@login_required
def daily_output():
    today         = date.today()
    default_from  = today - timedelta(days=13)
    date_7d       = today - timedelta(days=6)
    date_from_str = request.args.get('date_from', default_from.isoformat())
    date_to_str   = request.args.get('date_to',   today.isoformat())
    try:
        date_from = date.fromisoformat(date_from_str)
        date_to   = date.fromisoformat(date_to_str)
    except ValueError:
        date_from = default_from
        date_to   = today

    # Department totals (cards + row totals)
    section_summary = (
        db.session.query(
            ProductionOutput.op_desc,
            func.sum(ProductionOutput.labor_qty).label('total_qty'),
            func.count(ProductionOutput.id).label('entries'),
        )
        .filter(ProductionOutput.clock_in_date >= date_from, ProductionOutput.clock_in_date <= date_to)
        .group_by(ProductionOutput.op_desc)
        .order_by(func.sum(ProductionOutput.labor_qty).desc())
        .all()
    )

    # Pivot: date × department
    by_day_dept = (
        db.session.query(
            ProductionOutput.clock_in_date,
            ProductionOutput.op_desc,
            func.sum(ProductionOutput.labor_qty).label('total_qty'),
        )
        .filter(ProductionOutput.clock_in_date >= date_from, ProductionOutput.clock_in_date <= date_to)
        .group_by(ProductionOutput.clock_in_date, ProductionOutput.op_desc)
        .order_by(ProductionOutput.clock_in_date, ProductionOutput.op_desc)
        .all()
    )

    # Build pivot structures (plain dicts for template safety)
    _pivot     = defaultdict(dict)   # {dept: {date_str: qty}}
    _dept_tot  = defaultdict(float)  # {dept: total}
    _date_tot  = defaultdict(float)  # {date_str: total}
    for row in by_day_dept:
        d_str = row.clock_in_date.isoformat() if row.clock_in_date else ''
        dept  = row.op_desc or '—'
        qty   = float(row.total_qty or 0)
        _pivot[dept][d_str] = qty
        _dept_tot[dept]     += qty
        _date_tot[d_str]    += qty

    # Full calendar date list for the chosen range
    num_days  = (date_to - date_from).days + 1
    date_list = [date_from + timedelta(days=i) for i in range(num_days)]

    # Departments sorted by flow_order (Admin → Departments), unset/unknown fall to end
    # Only departments with track=True are included
    _tracked = {
        d.name
        for d in DeptModel.query.filter_by(track=True).all()
    }
    _flow = {
        d.name: d.flow_order if d.flow_order is not None else 9999
        for d in DeptModel.query.all()
    }
    departments = sorted(
        (d for d in _dept_tot.keys() if not _tracked or d in _tracked),
        key=lambda d: (_flow.get(d, 9999), d),
    )
    pivot       = dict(_pivot)          # {dept: {date_str: qty}}
    dept_totals = dict(_dept_tot)
    date_totals = dict(_date_tot)

    # Chart.js data
    chart_labels    = [d.strftime('%d %b') for d in date_list]
    chart_date_keys = [d.isoformat() for d in date_list]
    PALETTE = [
        '#4361ee', '#f72585', '#4cc9f0', '#06d6a0', '#ffd166',
        '#ef476f', '#118ab2', '#7209b7', '#adb5bd', '#3a0ca3',
        '#b5838d', '#6d6875', '#073b4c', '#e5989b',
    ]
    chart_dept_data = [
        {
            'label': dept,
            'data': [pivot.get(dept, {}).get(dk, 0) for dk in chart_date_keys],
            'backgroundColor': PALETTE[i % len(PALETTE)],
        }
        for i, dept in enumerate(departments)
    ]

    # Daily target
    target_qty  = SystemSetting.get_int(SETTING_DAILY_OUTPUT_TARGET, default=128)
    _tdays_str  = SystemSetting.get(SETTING_DAILY_OUTPUT_TARGET_DAYS, '0,1,2,3')
    target_days = {int(d) for d in _tdays_str.split(',') if d.strip().isdigit()}
    day_targets     = {d.isoformat(): (target_qty if d.weekday() in target_days else 0) for d in date_list}
    period_target   = sum(day_targets.values())
    chart_target_data = [day_targets.get(dk, 0) for dk in chart_date_keys]

    # Detail table
    page     = request.args.get('page', 1, type=int)
    section  = request.args.get('section', '')
    detail_q = ProductionOutput.query.filter(
        ProductionOutput.clock_in_date >= date_from,
        ProductionOutput.clock_in_date <= date_to,
    )
    if section:
        detail_q = detail_q.filter(ProductionOutput.op_desc == section)
    detail = detail_q.order_by(
        ProductionOutput.clock_in_date.desc(), ProductionOutput.op_desc
    ).paginate(page=page, per_page=100, error_out=False)

    total_qty = sum(r.total_qty or 0 for r in section_summary)
    last      = ProductionOutput.query.order_by(ProductionOutput.imported_at.desc()).first()

    return render_template(
        'operations/daily_output.html',
        title='Daily Output',
        date_from=date_from, date_to=date_to, today=today, date_7d=date_7d,
        section_summary=section_summary,
        detail=detail, section=section,
        total_qty=total_qty,
        last_imported=last.imported_at if last else None,
        date_list=date_list,
        departments=departments,
        pivot=pivot,
        dept_totals=dept_totals,
        date_totals=date_totals,
        chart_labels=chart_labels,
        chart_dept_data=chart_dept_data,
        chart_target_data=chart_target_data,
        day_targets=day_targets,
        target_qty=target_qty,
        period_target=period_target,
    )
