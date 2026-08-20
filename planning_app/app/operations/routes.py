'''Operations department portal routes.'''

from collections import defaultdict
import calendar
import csv
import io
from datetime import date, timedelta
from flask import current_app, flash, render_template, request, Response
from flask_login import current_user, login_required
from app.core.decorators import permission_required
from sqlalchemy import func
from . import operations_bp
from .models import WorksOrder, ProductionOutput
from app.extensions import db
from app.sales.orders.models import Department as DeptModel
from app.admin.models import SystemSetting, SETTING_DAILY_OUTPUT_TARGET, SETTING_DAILY_OUTPUT_TARGET_DAYS

@operations_bp.route('/')
@operations_bp.route('/dashboard')
@login_required
@permission_required("view_orders")
def dashboard():
    return render_template('operations/dashboard.html', title='Operations')


@operations_bp.route('/wip')
@login_required
@permission_required("view_orders")
def wip_overview():
    today = date.today()

    # WIP overview shows released orders only.
    category = request.args.get('category', 'models').strip().lower()
    shortages_only = request.args.get('shortages_only', '0') == '1'
    _is_model  = db.and_(
        WorksOrder.model.isnot(None),
        WorksOrder.model != '',
        ~WorksOrder.model.ilike('%scatter%'),
    )
    _is_parts  = db.or_(
        WorksOrder.model.is_(None),
        WorksOrder.model == '',
        WorksOrder.model.ilike('%scatter%'),
    )
    _cat_filter = (_is_model,) if category == 'models' else ((_is_parts,) if category == 'parts' else ())

    # Search filter — read early so pivot/chart also respect it.
    search = request.args.get('q', '').strip()
    _search_filters = ()
    if search:
        term = f'%{search}%'
        _search_filters = (db.or_(
            WorksOrder.job_num.ilike(term),
            WorksOrder.customer_name.ilike(term),
            WorksOrder.description.ilike(term),
            WorksOrder.model.ilike(term),
        ),)

    _base = (
        WorksOrder.assembly_seq == 0,
        WorksOrder.job_released == True,
        db.or_(WorksOrder.job_complete == False, WorksOrder.job_complete.is_(None)),
        WorksOrder.next_op.isnot(None),
        WorksOrder.next_op != '',
    ) + _cat_filter + _search_filters

    # ── Summary counts ────────────────────────────────────────────────
    from app.purchasing.materials.models import MaterialRequirementMain, MrpExemptMaterial
    from app.purchasing.materials.services import get_so_material_status, get_so_component_status, get_job_material_status, get_job_component_status, MAT_STATUS_META

    total   = db.session.query(func.count(WorksOrder.id)).filter(*_base).scalar() or 0
    last    = WorksOrder.query.order_by(WorksOrder.imported_at.desc()).first()

    # ── Per-SO material availability (cumulative netted) ──────────────
    # Collect all order_nums from the filtered WIP (across all pages).
    _all_order_nums = [
        str(r.order_num)
        for r in db.session.query(WorksOrder.order_num)
        .filter(*_base, WorksOrder.order_num.isnot(None))
        .distinct()
        .all()
        if r.order_num
    ]
    mat_status_map: dict[str, str] = get_so_material_status(_all_order_nums) if _all_order_nums else {}
    comp_status_map: dict[str, str] = get_so_component_status(_all_order_nums) if _all_order_nums else {}

    shortage_group = request.args.get('shortage_group', '')  # '' | 'fabric' | 'component' | 'either' | 'no_shortage'

    _fab_risk_sos  = {so for so, st in mat_status_map.items()  if st not in ("ok", "no_data")}
    _comp_risk_sos = {so for so, st in comp_status_map.items() if st not in ("ok", "no_data")}
    _fab_int  = {int(so) for so in _fab_risk_sos  if so.isdigit()}
    _comp_int = {int(so) for so in _comp_risk_sos if so.isdigit()}

    shortages = (
        db.session.query(func.count(WorksOrder.id))
        .filter(*_base, WorksOrder.order_num.in_(_fab_int))
        .scalar() or 0
    ) if _fab_int else 0
    comp_shortages = (
        db.session.query(func.count(WorksOrder.id))
        .filter(*_base, WorksOrder.order_num.in_(_comp_int))
        .scalar() or 0
    ) if _comp_int else 0

    _all_shortage_int = _fab_int | _comp_int
    either_shortages = (
        db.session.query(func.count(WorksOrder.id))
        .filter(*_base, WorksOrder.order_num.in_(_all_shortage_int))
        .scalar() or 0
    ) if _all_shortage_int else 0

    # Shortage-only filter — scope determined by shortage_group
    if shortage_group == 'component':
        _filter_int = _comp_int
    elif shortage_group == 'either':
        _filter_int = _fab_int | _comp_int
    elif shortage_group == 'no_shortage':
        _filter_int = _all_shortage_int  # used for exclusion
    else:
        _filter_int = _fab_int

    if shortages_only:
        if shortage_group == 'no_shortage':
            # Exclude orders with any confirmed shortage
            _shortage_filter = (WorksOrder.order_num.notin_(_all_shortage_int),) if _all_shortage_int else ()
        else:
            _shortage_filter = (WorksOrder.order_num.in_(_filter_int),) if _filter_int else ()
    else:
        _shortage_filter = ()
    _no_results = shortages_only and shortage_group != 'no_shortage' and not _filter_int

    # ── WIP pivot ─────────────────────────────────────────────────────
    OVERDUE = 'Overdue'
    if _no_results:
        overdue_rows = []
        current_rows = []
    else:
        overdue_rows = db.session.query(
            WorksOrder.next_op,
            func.count(WorksOrder.id).label('job_count'),
            func.sum(WorksOrder.required_qty).label('total_qty'),
        ).filter(
            *_base, *_shortage_filter,
            WorksOrder.next_op.isnot(None),
            WorksOrder.req_due_date < today,
        ).group_by(WorksOrder.next_op).all()

        current_rows = db.session.query(
            WorksOrder.next_op,
            WorksOrder.prod_plnwk,
            func.count(WorksOrder.id).label('job_count'),
            func.sum(WorksOrder.required_qty).label('total_qty'),
        ).filter(
            *_base, *_shortage_filter,
            WorksOrder.next_op.isnot(None),
            WorksOrder.prod_plnwk.isnot(None),
            db.or_(WorksOrder.req_due_date >= today, WorksOrder.req_due_date.is_(None)),
        ).group_by(WorksOrder.next_op, WorksOrder.prod_plnwk).all()

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

    # Departments ordered by production routing (flow_order).
    # Keyed by op_code (Epicor next_op) when set, falling back to name.
    _all_depts = DeptModel.query.all()
    _flow: dict[str, int] = {}
    for d in _all_depts:
        order = d.flow_order or 9999
        if d.op_code:
            _flow[d.op_code.upper()] = order
        _flow[d.name] = order   # fallback
    wip_ops = sorted(
        _op_tot.keys(),
        key=lambda op: (_flow.get(op.upper(), _flow.get(op, 9999)), op),
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
    page     = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    if per_page not in (25, 50, 100, 200):
        per_page = 50
    jobs = WorksOrder.query.filter(*_base, *_shortage_filter).order_by(
        WorksOrder.req_due_date.asc().nullslast(),
        WorksOrder.prod_plnwk.asc().nullslast(),
        WorksOrder.next_op.asc().nullslast(),
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )

    _page_job_nums = [str(j.job_num) for j in jobs.items if j.job_num]
    job_mat_map  = get_job_material_status(_page_job_nums)  if _page_job_nums else {}
    job_comp_map = get_job_component_status(_page_job_nums) if _page_job_nums else {}

    _cnt_rows = (
        db.session.query(WorksOrderComment.job_num, func.count(WorksOrderComment.id))
        .filter(WorksOrderComment.job_num.in_(_page_job_nums))
        .group_by(WorksOrderComment.job_num)
        .all()
    ) if _page_job_nums else []
    job_comment_counts = {r[0]: r[1] for r in _cnt_rows}

    # Partial order: order_nums where at least one other assembly_seq=0 job is complete.
    # Used to flag jobs where part of the same sales order has already shipped.
    _completed_order_nums = {
        row.order_num
        for row in db.session.query(WorksOrder.order_num)
        .filter(
            WorksOrder.assembly_seq == 0,
            WorksOrder.job_complete == True,
            WorksOrder.order_num.isnot(None),
        )
        .distinct()
        .all()
        if row.order_num
    }

    return render_template(
        'operations/wip_overview.html',
        title='Operations — WIP Overview',
        today=today,
        total=total,
        shortages=shortages,
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
        category=category,
        per_page=per_page,
        shortages_only=shortages_only,
        shortage_group=shortage_group,
        comp_shortages=comp_shortages,
        either_shortages=either_shortages,
        partial_order_nums=_completed_order_nums,
        mat_status_map=mat_status_map,
        comp_status_map=comp_status_map,
        job_comment_counts=job_comment_counts,
        job_mat_map=job_mat_map,
        job_comp_map=job_comp_map,
        mat_status_meta=MAT_STATUS_META,
    )

@operations_bp.route('/wip/export')
@login_required
@permission_required("view_orders")
@permission_required("export_data")
def wip_export():
    """Download WIP job detail as CSV, respecting the same category/search filters."""
    today = date.today()

    category = request.args.get('category', 'models').strip().lower()
    _is_model = db.and_(
        WorksOrder.model.isnot(None),
        WorksOrder.model != '',
        ~WorksOrder.model.ilike('%scatter%'),
    )
    _is_parts = db.or_(
        WorksOrder.model.is_(None),
        WorksOrder.model == '',
        WorksOrder.model.ilike('%scatter%'),
    )
    _cat_filter = (_is_model,) if category == 'models' else ((_is_parts,) if category == 'parts' else ())

    search = request.args.get('q', '').strip()
    _search_filters = ()
    if search:
        term = f'%{search}%'
        _search_filters = (db.or_(
            WorksOrder.job_num.ilike(term),
            WorksOrder.customer_name.ilike(term),
            WorksOrder.description.ilike(term),
            WorksOrder.model.ilike(term),
        ),)

    _base = (
        WorksOrder.assembly_seq == 0,
        WorksOrder.job_released == True,
        db.or_(WorksOrder.job_complete == False, WorksOrder.job_complete.is_(None)),
        WorksOrder.next_op.isnot(None),
        WorksOrder.next_op != '',
    ) + _cat_filter + _search_filters

    _completed_order_nums = {
        row.order_num
        for row in db.session.query(WorksOrder.order_num)
        .filter(WorksOrder.assembly_seq == 0, WorksOrder.job_complete == True, WorksOrder.order_num.isnot(None))
        .distinct().all()
        if row.order_num
    }

    shortages_only = request.args.get('shortages_only', '0') == '1'
    shortage_group = request.args.get('shortage_group', '')

    rows = WorksOrder.query.filter(*_base).order_by(
        WorksOrder.req_due_date.asc().nullslast(),
        WorksOrder.prod_plnwk.asc().nullslast(),
        WorksOrder.next_op.asc().nullslast(),
    ).all()

    from app.purchasing.materials.services import get_so_material_status, get_so_component_status, get_job_material_status, get_job_component_status, MAT_STATUS_META
    _order_nums = [str(job.order_num) for job in rows if job.order_num]
    mat_status_map  = get_so_material_status(_order_nums)  if _order_nums else {}
    comp_status_map = get_so_component_status(_order_nums) if _order_nums else {}

    if shortages_only:
        _fab_risk  = {so for so, st in mat_status_map.items()  if st not in ('ok', 'no_data')}
        _comp_risk = {so for so, st in comp_status_map.items() if st not in ('ok', 'no_data')}
        _fab_int   = {int(so) for so in _fab_risk  if so.isdigit()}
        _comp_int  = {int(so) for so in _comp_risk if so.isdigit()}
        _all_int   = _fab_int | _comp_int
        if shortage_group == 'no_shortage':
            rows = [j for j in rows if j.order_num not in _all_int]
        elif shortage_group == 'component':
            rows = [j for j in rows if j.order_num in _comp_int]
        elif shortage_group == 'either':
            rows = [j for j in rows if j.order_num in _all_int]
        else:
            rows = [j for j in rows if j.order_num in _fab_int]

    _page_job_nums = [str(j.job_num) for j in rows if j.job_num]
    job_mat_map  = get_job_material_status(_page_job_nums)  if _page_job_nums else {}
    job_comp_map = get_job_component_status(_page_job_nums) if _page_job_nums else {}

    # Build job notes map: latest comment per job, formatted as "user: body"
    _note_rows = (
        WorksOrderComment.query
        .filter(WorksOrderComment.job_num.in_(_page_job_nums))
        .order_by(WorksOrderComment.job_num, WorksOrderComment.created_at.asc())
        .all()
    ) if _page_job_nums else []
    job_notes_map: dict[str, str] = {}
    for _c in _note_rows:
        _entry = f"{_c.user.username if _c.user else 'deleted'}: {_c.body}"
        if _c.job_num in job_notes_map:
            job_notes_map[_c.job_num] += ' | ' + _entry
        else:
            job_notes_map[_c.job_num] = _entry

    _mat_label_map = {k: v[0] for k, v in MAT_STATUS_META.items()}

    def _fmt_plnwk(w):
        if not w:
            return ''
        try:
            return f'W{int(w[2:4]):02d}/{w[4:6]}'
        except (ValueError, IndexError):
            return w

    def _clean(text):
        """Strip embedded newlines/carriage-returns so Excel parses rows correctly."""
        if not text:
            return ''
        return ' '.join(text.splitlines())

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Job Number', 'Plan Week', 'Seq', 'Due Date', 'Order #',
        'Current Op', 'Model', 'Size', 'Customer',
        'Mat 1', 'OB Comments', 'GRN', 'Partial Order',
        'Job Fabric Status', 'Order Fabric Status', 'Job Comp. Status', 'Order Comp. Status',
        'Job Notes',
    ])
    for job in rows:
        is_partial = bool(job.order_num and job.order_num in _completed_order_nums and not job.job_complete)
        so_key     = str(job.order_num) if job.order_num else ''
        job_key    = str(job.job_num)   if job.job_num   else ''
        mat_st     = mat_status_map.get(so_key,  'no_data')
        comp_st    = comp_status_map.get(so_key, 'no_data')
        job_mat_st  = job_mat_map.get(job_key,  'no_data')
        job_comp_st = job_comp_map.get(job_key, 'no_data')
        def _label(st): return 'OK' if st in ('no_data', 'ok') else _mat_label_map.get(st, '')
        writer.writerow([
            job.job_num or '',
            job.prod_plnwk or '',
            _fmt_plnwk(job.prod_plnwk),
            job.req_due_date.strftime('%d/%m/%Y') if job.req_due_date else '',
            job.order_num or '',
            job.next_op or '',
            _clean(job.model),
            _clean(job.size_desc or job.size),
            _clean(job.customer_name),
            _clean(job.material_1_desc),
            _clean(job.order_book_comments),
            _clean(job.grn),
            'Yes' if is_partial else '',
            _label(job_mat_st),
            _label(mat_st),
            _label(job_comp_st),
            _label(comp_st),
            job_notes_map.get(job.job_num or '', ''),
        ])

    filename = f'wip_jobs_{date.today().isoformat()}.csv'
    # UTF-8 BOM ensures Excel opens the file with correct encoding.
    csv_bytes = '\ufeff' + output.getvalue()
    return Response(
        csv_bytes.encode('utf-8'),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@operations_bp.route('/daily-output')
@login_required
@permission_required("view_orders")
def daily_output():
    today         = date.today()
    date_7d       = today - timedelta(days=6)
    date_from_str = request.args.get('date_from', today.isoformat())
    date_to_str   = request.args.get('date_to',   today.isoformat())
    try:
        date_from = date.fromisoformat(date_from_str)
        date_to   = date.fromisoformat(date_to_str)
    except ValueError:
        date_from = today
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
    _yesterday = today - timedelta(days=1)
    _sc.append({
        'group':     'now',
        'label':     'Yesterday',
        'date_from': _yesterday.isoformat(),
        'date_to':   _yesterday.isoformat(),
        'active':    date_from == _yesterday and date_to == _yesterday,
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
