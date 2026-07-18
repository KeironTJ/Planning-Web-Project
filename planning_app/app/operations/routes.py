'''Operations department portal routes.'''

from collections import defaultdict
import calendar
from datetime import date, timedelta
from flask import current_app, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func
from . import operations_bp
from .models import WorksOrder, ProductionOutput
from app.extensions import db
from app.sales.orders.models import Department as DeptModel
from app.admin.models import SystemSetting, SETTING_DAILY_OUTPUT_TARGET, SETTING_DAILY_OUTPUT_TARGET_DAYS

@operations_bp.route('/daily-output/sync', methods=['POST'])
@login_required
def daily_output_sync():
    """AJAX endpoint: run the incremental production output sync and return JSON."""
    from app.core.epicor_client import KineticClient
    from app.core.epicor_importers import REGISTRY

    try:
        with KineticClient.from_app(current_app._get_current_object()) as client:
            importer = REGISTRY['production_output'](client)
            batch = importer.run(triggered_by_id=current_user.id)
        return jsonify({
            'status':        'ok',
            'rows_inserted': batch.rows_inserted,
            'notes':         batch.notes or '',
        })
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500


@operations_bp.route('/')
@operations_bp.route('/dashboard')
@login_required
def dashboard():
    today = date.today()
    total     = db.session.query(func.count(WorksOrder.id)).scalar() or 0
    released  = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.job_released == True).scalar() or 0
    shortages = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.mtl_shortage == True).scalar() or 0
    waiting   = db.session.query(func.count(WorksOrder.id)).filter(WorksOrder.waiting_temp == True).scalar() or 0
    last = WorksOrder.query.order_by(WorksOrder.imported_at.desc()).first()

    # ── WIP pivot ─────────────────────────────────────────────────────
    # Overdue: req_due_date is in the past (regardless of prod_plnwk)
    OVERDUE = 'Overdue'
    overdue_rows = (
        db.session.query(
            WorksOrder.next_op,
            func.count(WorksOrder.id).label('job_count'),
            func.sum(WorksOrder.required_qty).label('total_qty'),
        )
        .filter(
            WorksOrder.assembly_seq == 0,
            WorksOrder.next_op.isnot(None),
            WorksOrder.req_due_date < today,
        )
        .group_by(WorksOrder.next_op)
        .all()
    )

    # Current / future: not yet overdue, group by planned week
    current_rows = (
        db.session.query(
            WorksOrder.next_op,
            WorksOrder.prod_plnwk,
            func.count(WorksOrder.id).label('job_count'),
            func.sum(WorksOrder.required_qty).label('total_qty'),
        )
        .filter(
            WorksOrder.assembly_seq == 0,
            WorksOrder.next_op.isnot(None),
            WorksOrder.prod_plnwk.isnot(None),
            db.or_(WorksOrder.req_due_date >= today, WorksOrder.req_due_date.is_(None)),
        )
        .group_by(WorksOrder.next_op, WorksOrder.prod_plnwk)
        .all()
    )

    _pivot    = defaultdict(dict)
    _op_tot   = defaultdict(lambda: {'jobs': 0, 'qty': 0.0})
    _week_tot = defaultdict(lambda: {'jobs': 0, 'qty': 0.0})

    for r in overdue_rows:
        qty = float(r.total_qty or 0)
        _pivot[r.next_op][OVERDUE] = {'jobs': r.job_count, 'qty': qty}
        _op_tot[r.next_op]['jobs'] += r.job_count
        _op_tot[r.next_op]['qty']  += qty
        _week_tot[OVERDUE]['jobs'] += r.job_count
        _week_tot[OVERDUE]['qty']  += qty

    for r in current_rows:
        qty = float(r.total_qty or 0)
        _pivot[r.next_op][r.prod_plnwk] = {'jobs': r.job_count, 'qty': qty}
        _op_tot[r.next_op]['jobs']          += r.job_count
        _op_tot[r.next_op]['qty']           += qty
        _week_tot[r.prod_plnwk]['jobs']     += r.job_count
        _week_tot[r.prod_plnwk]['qty']      += qty

    future_weeks = sorted(w for w in _week_tot if w != OVERDUE)
    wip_weeks    = ([OVERDUE] if OVERDUE in _week_tot else []) + future_weeks

    def _fmt_week(w):
        if w == OVERDUE:
            return OVERDUE
        try:
            return f'W{int(w[2:4]):02d}/{w[4:6]}'
        except (ValueError, IndexError):
            return w

    wip_chart_labels = [_fmt_week(w) for w in wip_weeks]

    # Departments: tracked first in flow_order, rest alphabetically after
    _all_depts = DeptModel.query.all()
    _flow      = {d.name: (d.flow_order or 9999) for d in _all_depts}
    _tracked   = {d.name for d in _all_depts if d.track}
    wip_ops    = sorted(
        _op_tot.keys(),
        key=lambda op: (0 if op in _tracked else 1, _flow.get(op, 9999), op),
    )

    PALETTE = [
        '#4361ee', '#f72585', '#4cc9f0', '#06d6a0', '#ffd166',
        '#ef476f', '#118ab2', '#7209b7', '#adb5bd', '#3a0ca3',
        '#b5838d', '#6d6875', '#073b4c', '#e5989b',
    ]
    wip_chart_datasets = [
        {
            'label': op,
            'data': [_pivot.get(op, {}).get(w, {}).get('qty', 0) for w in wip_weeks],
            'backgroundColor': PALETTE[i % len(PALETTE)],
        }
        for i, op in enumerate(wip_ops)
    ]

    # ── Job detail list ───────────────────────────────────────────────
    search = request.args.get('q', '').strip()
    page   = request.args.get('page', 1, type=int)
    q = WorksOrder.query.filter(WorksOrder.assembly_seq == 0)
    if search:
        term = f'%{search}%'
        q = q.filter(db.or_(
            WorksOrder.job_num.ilike(term),
            WorksOrder.customer_name.ilike(term),
            WorksOrder.description.ilike(term),
            WorksOrder.model.ilike(term),
        ))
    jobs = q.order_by(WorksOrder.prod_plnwk, WorksOrder.req_due_date).paginate(
        page=page, per_page=50, error_out=False
    )

    return render_template(
        'operations/dashboard.html',
        title='Operations',
        today=today,
        total=total, released=released, shortages=shortages, waiting=waiting,
        last_imported=last.imported_at if last else None,
        wip_pivot=dict(_pivot),
        wip_ops=wip_ops,
        wip_weeks=wip_weeks,
        op_totals={k: dict(v) for k, v in _op_tot.items()},
        week_totals={k: dict(v) for k, v in _week_tot.items()},
        wip_chart_datasets=wip_chart_datasets,
        wip_chart_labels=wip_chart_labels,
        jobs=jobs,
        search=search,
    )

@operations_bp.route('/daily-output')
@login_required
def daily_output():
    today         = date.today()
    default_from  = today - timedelta(days=today.weekday())   # Monday of current week
    date_7d       = today - timedelta(days=6)
    date_from_str = request.args.get('date_from', default_from.isoformat())
    date_to_str   = request.args.get('date_to',   today.isoformat())
    try:
        date_from = date.fromisoformat(date_from_str)
        date_to   = date.fromisoformat(date_to_str)
    except ValueError:
        date_from = default_from
        date_to   = today

    view = request.args.get('view', 'daily')

    # ── Smart period shortcuts ───────────────────────────────────────
    def _wb(d):
        mon = d - timedelta(days=d.weekday())
        return mon, mon + timedelta(days=6)

    _sc = []
    # "Now" shortcuts — shown first for quick access
    _mon_curr, _sun_curr = _wb(today)
    _sc.append({
        'group':     'now',
        'label':     'Today',
        'date_from': today.isoformat(),
        'date_to':   today.isoformat(),
        'active':    date_from == today and date_to == today,
    })
    _sc.append({
        'group':     'now',
        'label':     'This Week',
        'date_from': _mon_curr.isoformat(),
        'date_to':   _sun_curr.isoformat(),
        'active':    date_from == _mon_curr and date_to == _sun_curr,
    })
    # 3 prior ISO weeks (oldest → newest); current week is covered by "This Week" above
    for _i in range(3, 0, -1):
        _mon, _sun = _wb(today - timedelta(weeks=_i))
        _iso = _mon.isocalendar()
        _sfx = f" '{str(_iso[0])[2:]}" if _iso[0] != today.year else ""
        _sc.append({
            'group': 'week',
            'label': f"W{_iso[1]:02d}{_sfx}",
            'date_from': _mon.isoformat(),
            'date_to':   _sun.isoformat(),
            'active':    date_from == _mon and date_to == _sun,
        })
    # Current + 5 prior months (oldest → newest)
    for _i in range(5, -1, -1):
        _yr, _mo = today.year, today.month - _i
        while _mo <= 0:
            _mo += 12; _yr -= 1
        _mf = date(_yr, _mo, 1)
        _mt = date(_yr, _mo, calendar.monthrange(_yr, _mo)[1])
        _sfx = f" '{str(_yr)[2:]}" if _yr != today.year else ""
        _sc.append({
            'group': 'month',
            'label': _mf.strftime('%b') + _sfx,
            'date_from': _mf.isoformat(),
            'date_to':   _mt.isoformat(),
            'active':    date_from == _mf and date_to == _mt,
        })
    # Previous year + current year
    for _y in (today.year - 1, today.year):
        _sc.append({
            'group': 'year',
            'label': str(_y),
            'date_from': date(_y, 1, 1).isoformat(),
            'date_to':   date(_y, 12, 31).isoformat(),
            'active':    date_from == date(_y, 1, 1) and date_to == date(_y, 12, 31),
        })
    period_shortcuts = _sc
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

    # Department filter — parsed early so pivot / chart also respect it
    section = request.args.get('section', '')

    # Pivot: date × department
    _by_day_q = (
        db.session.query(
            ProductionOutput.clock_in_date,
            ProductionOutput.op_desc,
            func.sum(ProductionOutput.labor_qty).label('total_qty'),
        )
        .filter(ProductionOutput.clock_in_date >= date_from, ProductionOutput.clock_in_date <= date_to)
        .group_by(ProductionOutput.clock_in_date, ProductionOutput.op_desc)
        .order_by(ProductionOutput.clock_in_date, ProductionOutput.op_desc)
    )
    if section:
        _by_day_q = _by_day_q.filter(ProductionOutput.op_desc == section)
    by_day_dept = _by_day_q.all()

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
    detail_q = ProductionOutput.query.filter(
        ProductionOutput.clock_in_date >= date_from,
        ProductionOutput.clock_in_date <= date_to,
    )
    if section:
        detail_q = detail_q.filter(ProductionOutput.op_desc == section)
    detail = detail_q.order_by(
        ProductionOutput.clock_in_date.desc(), ProductionOutput.op_desc
    ).paginate(page=page, per_page=100, error_out=False)

    # total_qty reflects the active dept filter (or all depts if no filter)
    total_qty = (
        float(dept_totals.get(section, 0))
        if section else
        sum(r.total_qty or 0 for r in section_summary)
    )
    last      = ProductionOutput.query.order_by(ProductionOutput.imported_at.desc()).first()

    # ── Weekly pivot (reuse by_day_dept query data) ──────────────────────
    _week_pivot    = defaultdict(lambda: defaultdict(float))
    _week_date_tot = defaultdict(float)

    for row in by_day_dept:
        if not row.clock_in_date:
            continue
        iso      = row.clock_in_date.isocalendar()
        week_key = f"{iso[0]}-W{iso[1]:02d}"
        dept     = row.op_desc or '—'
        qty      = float(row.total_qty or 0)
        _week_pivot[dept][week_key] += qty
        _week_date_tot[week_key]    += qty

    week_list = sorted(_week_date_tot.keys())

    def _week_label(wk):
        return f"W{int(wk[6:]):02d}/{wk[2:4]}"

    chart_week_labels = [_week_label(wk) for wk in week_list]

    week_targets = {
        wk: sum(
            target_qty
            for d in date_list
            if f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}" == wk
            and d.weekday() in target_days
        )
        for wk in week_list
    }
    week_period_target     = sum(week_targets.values())
    chart_week_target_data = [week_targets.get(wk, 0) for wk in week_list]

    week_pivot  = {dept: dict(inner) for dept, inner in _week_pivot.items()}
    week_totals = dict(_week_date_tot)

    # ── Trendlines ───────────────────────────────────────────────
    def _sma(vals, w):
        """Simple moving average; None for positions with insufficient history."""
        out = []
        for i in range(len(vals)):
            out.append(None if i < w - 1 else round(sum(vals[i - w + 1:i + 1]) / w, 1))
        return out

    def _lintrend(vals):
        """Ordinary least-squares linear regression through (i, vals[i])."""
        n = len(vals)
        if n < 2:
            return [round(v, 1) for v in vals]
        xm = (n - 1) / 2.0
        ym = sum(vals) / n
        num = sum((i - xm) * (vals[i] - ym) for i in range(n))
        den = sum((i - xm) ** 2 for i in range(n))
        if den == 0:
            return [round(ym, 1)] * n
        slope = num / den
        return [round(ym + slope * (i - xm), 1) for i in range(n)]

    # Trendline source: filtered dept when section is set, otherwise the final
    # department in the process flow (last entry in flow-ordered departments list).
    _trend_dept = section or (departments[-1] if departments else None)
    if _trend_dept and not section:
        _dv = [float(pivot.get(_trend_dept, {}).get(d.isoformat(), 0)) for d in date_list]
        _wv = [float(week_pivot.get(_trend_dept, {}).get(wk, 0)) for wk in week_list]
    else:
        _dv = [float(date_totals.get(d.isoformat(), 0)) for d in date_list]
        _wv = [float(week_totals.get(wk, 0)) for wk in week_list]
    chart_trendline_data      = _lintrend(_dv)
    chart_week_trendline_data = _lintrend(_wv)
    trend_dept_label          = _trend_dept or ''

    chart_week_dept_data = [
        {
            'label':           dept,
            'data':            [week_pivot.get(dept, {}).get(wk, 0) for wk in week_list],
            'backgroundColor': PALETTE[i % len(PALETTE)],
        }
        for i, dept in enumerate(departments)
    ]

    return render_template(
        'operations/daily_output.html',
        title='Production Output',
        view=view,
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
        week_list=week_list,
        week_pivot=week_pivot,
        week_totals=week_totals,
        chart_week_labels=chart_week_labels,
        chart_week_dept_data=chart_week_dept_data,
        chart_week_target_data=chart_week_target_data,
        week_targets=week_targets,
        week_period_target=week_period_target,
        period_shortcuts=period_shortcuts,
        chart_trendline_data=chart_trendline_data,
        chart_week_trendline_data=chart_week_trendline_data,
        trend_dept_label=trend_dept_label,
    )
