'''Operations department portal routes.'''

from flask import render_template, request
from flask_login import login_required

from app.core.decorators import permission_required
from . import operations_bp
from .services import (
    get_daily_output,
    get_quick_wins_export,
    get_wip_export,
    get_wip_overview,
)


@operations_bp.route('/')
@operations_bp.route('/dashboard')
@login_required
@permission_required('view_orders')
def dashboard():
    return render_template('operations/dashboard.html', title='Operations')


@operations_bp.route('/wip')
@login_required
@permission_required('view_orders')
def wip_overview():
    return render_template('operations/wip_overview.html', title='Operations — WIP Overview', **get_wip_overview(request.args))


@operations_bp.route('/wip/export')
@login_required
@permission_required('view_orders')
@permission_required('export_data')
def wip_export():
    return get_wip_export(request.args)


@operations_bp.route('/wip/quick-wins/export')
@login_required
@permission_required('view_orders')
@permission_required('export_data')
def quick_wins_export():
    return get_quick_wins_export(request.args)


@operations_bp.route('/daily-output')
@login_required
@permission_required('view_orders')
def daily_output():
    return render_template('operations/daily_output.html', title='Production Output', **get_daily_output(request.args))