"""Operations REST endpoints."""

from datetime import datetime, timezone

from flask import jsonify, request
from flask_login import login_required, current_user

from . import api_v1_bp
from app.core.decorators import permission_required
from app.extensions import db
from app.operations.models import WorksOrderComment


# ---------------------------------------------------------------------------
# Production output sync
# ---------------------------------------------------------------------------

@api_v1_bp.route('/operations/daily-output/sync', methods=['POST'])
@login_required
@permission_required('manage_orders')
def daily_output_sync():
    """Trigger incremental production output sync; returns JSON progress."""
    from flask import current_app, flash
    from app.core.epicor_client import KineticClient
    from app.core.epicor_importers import REGISTRY

    try:
        with KineticClient.from_app(current_app._get_current_object()) as client:
            importer = REGISTRY['production_output'](client)
            sync_params = importer.get_dynamic_params()
            batch = importer.run(params=sync_params, triggered_by_id=current_user.id)
        date_from  = sync_params.get('DateFrom', '')
        date_to    = sync_params.get('DateTo',   '')
        date_range = f'{date_from} \u2192 {date_to}' if date_from and date_to else ''
        flash(
            'Production output sync complete'
            + (f' \u00b7 {date_range}' if date_range else '')
            + f' \u00b7 {batch.row_count} fetched, {batch.rows_inserted} inserted'
            + (f' \u00b7 {batch.notes}' if batch.notes else ''),
            'success',
        )
        return jsonify({
            'status':        'ok',
            'rows_inserted': batch.rows_inserted,
            'row_count':     batch.row_count,
            'notes':         batch.notes or '',
            'date_from':     date_from,
            'date_to':       date_to,
        })
    except Exception as exc:
        flash(f'Production output sync failed: {exc}', 'danger')
        return jsonify({'status': 'error', 'message': str(exc)}), 500


# ---------------------------------------------------------------------------
# Works order (job) comments
# ---------------------------------------------------------------------------

@api_v1_bp.route('/operations/jobs/<job_num>/comments', methods=['GET'])
@login_required
@permission_required('view_orders')
def job_comments(job_num):
    comments = (WorksOrderComment.query
                .filter_by(job_num=job_num)
                .order_by(WorksOrderComment.created_at.asc())
                .all())
    return jsonify({
        'ok': True,
        'comments': [
            {
                'id':         c.id,
                'user':       c.user.username if c.user else 'deleted',
                'user_id':    c.user_id,
                'body':       c.body,
                'created_at': c.created_at.strftime('%d %b %Y %H:%M'),
                'updated_at': c.updated_at.strftime('%d %b %Y %H:%M') if c.updated_at else None,
                'can_edit':   c.user_id == current_user.id or current_user.is_admin,
            }
            for c in comments
        ],
    })


@api_v1_bp.route('/operations/jobs/<job_num>/comments', methods=['POST'])
@login_required
@permission_required('update_order_status')
def add_job_comment(job_num):
    body = request.form.get('body', '').strip()
    if not body:
        return jsonify({'ok': False, 'error': 'Comment cannot be blank.'}), 400
    if len(body) > 1000:
        return jsonify({'ok': False, 'error': 'Comment cannot exceed 1000 characters.'}), 400
    comment = WorksOrderComment(job_num=job_num, user_id=current_user.id, body=body)
    db.session.add(comment)
    db.session.commit()
    return jsonify({
        'ok': True,
        'comment': {
            'id':         comment.id,
            'user':       current_user.username,
            'user_id':    comment.user_id,
            'body':       comment.body,
            'created_at': comment.created_at.strftime('%d %b %Y %H:%M'),
            'updated_at': None,
            'can_edit':   True,
        },
    })


@api_v1_bp.route('/operations/jobs/comments/<int:comment_id>', methods=['PATCH'])
@login_required
@permission_required('update_order_status')
def edit_job_comment(comment_id):
    comment = db.session.get(WorksOrderComment, comment_id)
    if comment is None:
        return jsonify({'ok': False, 'error': 'Not found.'}), 404
    if comment.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'ok': False, 'error': 'Not authorised.'}), 403
    body = request.form.get('body', '').strip()
    if not body:
        return jsonify({'ok': False, 'error': 'Comment cannot be blank.'}), 400
    if len(body) > 1000:
        return jsonify({'ok': False, 'error': 'Comment cannot exceed 1000 characters.'}), 400
    comment.body       = body
    comment.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({
        'ok': True,
        'comment': {
            'id':         comment.id,
            'body':       comment.body,
            'updated_at': comment.updated_at.strftime('%d %b %Y %H:%M'),
        },
    })


@api_v1_bp.route('/operations/jobs/comments/<int:comment_id>', methods=['DELETE'])
@login_required
@permission_required('update_order_status')
def delete_job_comment(comment_id):
    comment = db.session.get(WorksOrderComment, comment_id)
    if comment is None:
        return jsonify({'ok': False, 'error': 'Not found.'}), 404
    if comment.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'ok': False, 'error': 'Not authorised.'}), 403
    db.session.delete(comment)
    db.session.commit()
    return jsonify({'ok': True})
