"""
Admin blueprint routes.

All routes here require the "admin" role.  The admin_required decorator
from core.decorators enforces this at the HTTP layer.
"""

import io
from datetime import datetime, timezone

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from . import admin_bp
from .forms import ImportUploadForm, DeptHoursForm, SystemSettingsForm, DeptCreateForm
from .models import SystemSetting, SETTING_AUTO_COMPLETE_DESPATCH, SETTING_DAILY_OUTPUT_TARGET, SETTING_DAILY_OUTPUT_TARGET_DAYS, SETTING_MRP_LEAD_DAYS, SETTING_FABRIC_CLASS_IDS, SETTING_COMPONENT_CLASS_IDS, SETTING_MRP_COMPONENT_LEAD_DAYS
from app.auth.models import User, Role, Permission, AuditLog
from app.auth.services import RoleService
from app.extensions import db
from app.core.decorators import admin_required, permission_required
from app.sales.orders.models import Department, ImportBatch


# ---------------------------------------------------------------------------
# Epicor Data Sync
# ---------------------------------------------------------------------------

@admin_bp.route("/epicor-sync")
@login_required
@admin_required
def epicor_sync():
    """Show sync status for every registered Epicor BAQ importer."""
    from app.core.epicor_importers import REGISTRY
    from datetime import date

    last_syncs = {}
    for key, cls in REGISTRY.items():
        batch = (
            ImportBatch.query
            .filter_by(import_type=cls.IMPORT_TYPE)
            .order_by(ImportBatch.uploaded_at.desc())
            .first()
        )
        last_syncs[key] = {"baq_name": cls.BAQ_NAME, "batch": batch}

    today = date.today()
    defaults = {
        "sales_closed_from":      date(today.year, 1, 1).isoformat(),
        "sales_closed_to":        today.isoformat(),
        "production_output_from": (today - __import__("datetime").timedelta(days=7)).isoformat(),
        "production_output_to":   today.isoformat(),
    }

    return render_template(
        "admin/epicor_sync.html",
        title="Epicor Data Sync",
        last_syncs=last_syncs,
        defaults=defaults,
    )


@admin_bp.route("/epicor-sync/run", methods=["POST"])
@login_required
@admin_required
def epicor_sync_run():
    """Trigger one or all BAQ importers (traditional form POST fallback)."""
    from flask import current_app
    from app.core.epicor_client import KineticClient
    from app.core.epicor_importers import REGISTRY, run_batch

    baq_key = request.form.get("baq_key") or None
    if baq_key and baq_key not in REGISTRY:
        flash(f"Unknown BAQ key: {baq_key!r}", "danger")
        return redirect(url_for("admin.epicor_sync"))
    keys = [baq_key] if baq_key else None

    extra_params: dict = {}
    if baq_key == "sales_closed":
        from datetime import date as _date
        def _fmt(iso):
            try: return _date.fromisoformat(iso).strftime("%d/%m/%Y")
            except (ValueError, TypeError): return iso
        extra_params = {
            "OrderDateFrom": _fmt(request.form.get("OrderDateFrom", "")),
            "OrderDateTo":   _fmt(request.form.get("OrderDateTo", "")),
        }
    elif baq_key == "production_output":
        extra_params = {
            "DateFrom": request.form.get("DateFrom", ""),
            "DateTo":   request.form.get("DateTo", ""),
        }

    try:
        with KineticClient.from_app(current_app._get_current_object()) as client:
            if baq_key and extra_params:
                batch = REGISTRY[baq_key](client).run(
                    params=extra_params, triggered_by_id=current_user.id
                )
                results = {baq_key: batch}
            else:
                results = run_batch(client, keys=keys, triggered_by_id=current_user.id)
    except Exception as exc:
        flash(f"Could not connect to Epicor: {exc}", "danger")
        return redirect(url_for("admin.epicor_sync"))

    for key, result in results.items():
        if isinstance(result, Exception):
            flash(f"{key}: {result}", "danger")
        else:
            flash(f"{key}: {result.row_count} fetched / {result.rows_inserted} inserted.", "success")

    return redirect(url_for("admin.epicor_sync"))


@admin_bp.route("/epicor-sync/run-one", methods=["POST"])
@login_required
@admin_required
def epicor_sync_run_one():
    """
    AJAX endpoint: run a single importer and return JSON.

    Expects JSON body: {"baq_key": "stock", "params": {"DateFrom": "2026-01-01"}}
    Returns:          {"status": "ok", "row_count": 123, "rows_inserted": 123}
    """
    from flask import current_app, jsonify
    from app.core.epicor_client import KineticClient
    from app.core.epicor_importers import REGISTRY

    data    = request.get_json(force=True, silent=True) or {}
    baq_key = data.get("baq_key", "")
    params  = data.get("params", {}) or {}

    if not baq_key or baq_key not in REGISTRY:
        return jsonify({"status": "error", "message": f"Unknown importer: {baq_key!r}"}), 400

    # Convert sales_closed date params from ISO to UK format
    if baq_key == "sales_closed":
        from datetime import date as _date
        def _to_uk(iso):
            try: return _date.fromisoformat(iso).strftime("%d/%m/%Y")
            except (ValueError, TypeError): return iso
        if "OrderDateFrom" in params: params["OrderDateFrom"] = _to_uk(params["OrderDateFrom"])
        if "OrderDateTo"   in params: params["OrderDateTo"]   = _to_uk(params["OrderDateTo"])

    try:
        import time
        from sqlalchemy.exc import OperationalError as _OE
        last_exc = None
        for attempt in range(4):           # up to 4 attempts: 0, 2, 4, 8 s backoff
            if attempt:
                time.sleep(2 ** attempt)   # 2, 4, 8 seconds
            try:
                with KineticClient.from_app(current_app._get_current_object()) as client:
                    importer = REGISTRY[baq_key](client)
                    batch = importer.run(
                        params=params if params else None,
                        triggered_by_id=current_user.id,
                    )
                # Build a human-readable summary for the flash message.
                date_info = ""
                if baq_key == "production_output" and params.get("DateFrom"):
                    date_info = f" · {params['DateFrom']} → {params.get('DateTo', '')}"
                elif baq_key == "sales_closed" and params.get("OrderDateFrom"):
                    date_info = f" · {params['OrderDateFrom']} → {params.get('OrderDateTo', '')}"
                flash(
                    f"{baq_key} sync complete{date_info}"
                    f" · {batch.row_count} fetched, {batch.rows_inserted} inserted"
                    + (f" · {batch.notes}" if batch.notes else ""),
                    "success",
                )
                return jsonify({
                    "status":        "ok",
                    "key":           baq_key,
                    "row_count":     batch.row_count,
                    "rows_inserted": batch.rows_inserted,
                    "notes":         batch.notes or "",
                })
            except _OE as db_err:
                last_exc = db_err
                continue   # retry on SQLite lock
        flash(f"{baq_key}: DB locked after retries — {last_exc}", "danger")
        return jsonify({"status": "error", "key": baq_key,
                        "message": f"DB locked after retries: {last_exc}"}), 500
    except Exception as exc:
        flash(f"{baq_key} sync failed: {exc}", "danger")
        return jsonify({"status": "error", "key": baq_key, "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Sync Jobs  (job-based grouped schedules)
# ---------------------------------------------------------------------------

@admin_bp.route("/epicor-sync/schedules")
@login_required
@admin_required
def sync_schedules():
    """List all sync jobs."""
    from app.core.epicor_importers import REGISTRY
    from app.admin.models import SyncJob

    jobs = SyncJob.query.order_by(SyncJob.created_at).all()
    return render_template(
        "admin/schedules.html",
        title="Sync Jobs",
        jobs=jobs,
        registry=REGISTRY,
    )


@admin_bp.route("/epicor-sync/schedules/jobs", methods=["POST"])
@login_required
@admin_required
def sync_job_create():
    """Create a new sync job (traditional form POST → redirect)."""
    from app.admin.models import SyncJob

    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Job name is required.", "danger")
        return redirect(url_for("admin.sync_schedules"))

    try:
        interval = int(request.form.get("interval_minutes", 120))
        if interval < 1:
            raise ValueError
    except (ValueError, TypeError):
        interval = 120

    job = SyncJob(name=name, interval_minutes=interval)
    db.session.add(job)
    db.session.commit()
    flash(f"Job '{job.name}' created.", "success")
    return redirect(url_for("admin.sync_schedules"))


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>", methods=["POST"])
@login_required
@admin_required
def sync_job_update(job_id: int):
    """
    AJAX: update job fields (name, enabled, interval_minutes).

    Expects JSON: {"name": "...", "enabled": true, "interval_minutes": 60}
    """
    from app.admin.models import SyncJob

    job  = SyncJob.query.get_or_404(job_id)
    data = request.get_json(force=True, silent=True) or {}

    if "name" in data:
        name = (data["name"] or "").strip()
        if name:
            job.name = name

    if "enabled" in data:
        job.enabled = bool(data["enabled"])
        if job.enabled:
            # Set next_run_at to now so the scheduler picks it up on the very
            # next tick (within 60 s) rather than waiting a full interval.
            job.next_run_at = datetime.now(timezone.utc)
        else:
            job.next_run_at = None

    if "interval_minutes" in data:
        try:
            mins = int(data["interval_minutes"])
            if mins < 1:
                raise ValueError
            job.interval_minutes = mins
            if job.enabled:
                job.schedule_next_run()
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "interval_minutes must be a positive integer"}), 400

    # Allow the live-run JS to record overall job outcome after sequential item runs
    if "last_status" in data:
        job.last_status  = data["last_status"]
        job.last_run_at  = datetime.now(timezone.utc)
        if job.enabled:
            job.schedule_next_run()

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(exc)}), 500

    return jsonify({
        "status":           "ok",
        "id":               job.id,
        "name":             job.name,
        "enabled":          job.enabled,
        "interval_minutes": job.interval_minutes,
        "next_run_at":      job.next_run_at.isoformat() if job.next_run_at else None,
    })


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/delete", methods=["POST"])
@login_required
@admin_required
def sync_job_delete(job_id: int):
    """AJAX: delete a job and all its items."""
    from app.admin.models import SyncJob

    job = SyncJob.query.get_or_404(job_id)
    db.session.delete(job)
    db.session.commit()
    return jsonify({"status": "ok"})


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/run-now", methods=["POST"])
@login_required
@admin_required
def sync_job_run_now(job_id: int):
    """AJAX: start a job immediately in a background thread.

    Claims the job (sets is_running=True) before returning so the very
    first status poll sees the running state with no race window.
    The thread then runs the importers and clears is_running when done.
    """
    from flask import current_app
    from datetime import timedelta
    from app.admin.models import SyncJob
    from app.core.scheduler import run_job_in_thread

    job = SyncJob.query.get_or_404(job_id)

    if job.is_running:
        return jsonify({"status": "already_running", "message": "Job is already running."})

    # Claim the job synchronously so the DB reflects is_running=True
    # before the route returns and the frontend starts polling.
    job.is_running = True
    if job.enabled:
        job.next_run_at = datetime.now(timezone.utc) + timedelta(minutes=job.interval_minutes)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(exc)}), 500

    run_job_in_thread(current_app._get_current_object(), job.id)
    return jsonify({"status": "started", "job_id": job.id})


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/status", methods=["GET"])
@login_required
@admin_required
def sync_job_status(job_id: int):
    """AJAX: return current job and item status for frontend polling."""
    from app.admin.models import SyncJob

    job = SyncJob.query.get_or_404(job_id)

    def _iso(dt):
        """Return an ISO string with explicit UTC marker for JS Date parsing."""
        if dt is None:
            return None
        s = dt.isoformat()
        # SQLite returns naive datetimes (no +00:00); add Z so JS parses as UTC.
        if s[-1] not in ('+', 'Z') and '+' not in s[-6:]:
            s += 'Z'
        return s

    return jsonify({
        "status":        "ok",
        "is_running":    job.is_running,
        "last_status":   job.last_status,
        "last_run_at":   _iso(job.last_run_at),
        "next_run_at":   _iso(job.next_run_at),
        "items": [
            {
                "id":             item.id,
                "importer_key":   item.importer_key,
                "last_status":    item.last_status,
                "last_row_count": item.last_row_count,
                "last_error":     item.last_error,
                "last_run_at":    _iso(item.last_run_at),
            }
            for item in job.items
        ],
    })


@admin_bp.route("/epicor-sync/schedules/status", methods=["GET"])
@login_required
@admin_required
def sync_schedules_status():
    """AJAX: lightweight bulk status for all jobs — used by the 30-second
    auto-refresh so the page does not need to re-render the full template."""
    from app.admin.models import SyncJob

    def _iso(dt):
        if dt is None:
            return None
        s = dt.isoformat()
        if s[-1] not in ('+', 'Z') and '+' not in s[-6:]:
            s += 'Z'
        return s

    jobs = SyncJob.query.order_by(SyncJob.created_at).all()
    return jsonify({
        "status": "ok",
        "jobs": [
            {
                "id":          job.id,
                "is_running":  job.is_running,
                "last_status": job.last_status,
                "last_run_at": _iso(job.last_run_at),
                "next_run_at": _iso(job.next_run_at),
                "items": [
                    {
                        "id":             item.id,
                        "last_status":    item.last_status,
                        "last_row_count": item.last_row_count,
                        "last_error":     item.last_error,
                        "last_run_at":    _iso(item.last_run_at),
                    }
                    for item in job.items
                ],
            }
            for job in jobs
        ],
    })


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/items", methods=["POST"])
@login_required
@admin_required
def sync_job_item_add(job_id: int):
    """AJAX: add an importer to a job."""
    from app.admin.models import SyncJob, SyncJobItem
    from app.core.epicor_importers import REGISTRY

    job  = SyncJob.query.get_or_404(job_id)
    data = request.get_json(force=True, silent=True) or {}
    key  = data.get("importer_key", "")

    if not key or key not in REGISTRY:
        return jsonify({"status": "error", "message": f"Unknown importer: {key!r}"}), 400

    # Determine next sort_order
    max_order = db.session.query(db.func.max(SyncJobItem.sort_order)).filter_by(job_id=job_id).scalar() or -1
    item = SyncJobItem(job_id=job_id, importer_key=key, sort_order=max_order + 1)
    db.session.add(item)
    db.session.commit()

    return jsonify({
        "status":        "ok",
        "item_id":       item.id,
        "importer_key":  key,
        "display_name":  item.display_name,
        "sort_order":    item.sort_order,
        "baq_name":      REGISTRY[key].BAQ_NAME,
    })


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/items/<int:item_id>", methods=["POST"])
@login_required
@admin_required
def sync_job_item_update(job_id: int, item_id: int):
    """AJAX: update item params or reorder job items."""
    import json as _json
    from app.admin.models import SyncJobItem

    item = SyncJobItem.query.filter_by(id=item_id, job_id=job_id).first_or_404()
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action", "save_params")

    if action == "reorder":
        item_ids = data.get("item_ids")
        if (
            not isinstance(item_ids, list)
            or any(not isinstance(value, int) or isinstance(value, bool) for value in item_ids)
            or len(item_ids) != len(set(item_ids))
        ):
            return jsonify({"status": "error", "message": "Invalid item order"}), 400

        job_items = SyncJobItem.query.filter_by(job_id=job_id).all()
        items_by_id = {job_item.id: job_item for job_item in job_items}
        if set(item_ids) != set(items_by_id):
            return jsonify({"status": "error", "message": "Item order does not match this job"}), 400

        for sort_order, ordered_item_id in enumerate(item_ids):
            items_by_id[ordered_item_id].sort_order = sort_order

    elif action in ("move_up", "move_down"):
        # Find the adjacent item to swap sort_order with
        if action == "move_up":
            sibling = (
                SyncJobItem.query
                .filter(SyncJobItem.job_id == job_id, SyncJobItem.sort_order < item.sort_order)
                .order_by(SyncJobItem.sort_order.desc())
                .first()
            )
        else:
            sibling = (
                SyncJobItem.query
                .filter(SyncJobItem.job_id == job_id, SyncJobItem.sort_order > item.sort_order)
                .order_by(SyncJobItem.sort_order.asc())
                .first()
            )
        if sibling:
            item.sort_order, sibling.sort_order = sibling.sort_order, item.sort_order

    elif action == "save_params":
        raw = data.get("schedule_params")
        if raw is None or raw == {}:
            item.schedule_params = None
        else:
            try:
                item.schedule_params = _json.dumps(raw)
            except (TypeError, ValueError) as exc:
                return jsonify({"status": "error", "message": f"Invalid params: {exc}"}), 400

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(exc)}), 500

    return jsonify({
        "status":       "ok",
        "item_id":      item.id,
        "sort_order":   item.sort_order,
        "params_label": item.params_label,
    })


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/items/<int:item_id>/delete", methods=["POST"])
@login_required
@admin_required
def sync_job_item_delete(job_id: int, item_id: int):
    """AJAX: remove an item from a job."""
    from app.admin.models import SyncJobItem

    item = SyncJobItem.query.filter_by(id=item_id, job_id=job_id).first_or_404()
    key = item.importer_key
    db.session.delete(item)
    db.session.commit()
    return jsonify({"status": "ok", "importer_key": key})


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/items/<int:item_id>/run-one", methods=["POST"])
@login_required
@admin_required
def sync_job_item_run_one(job_id: int, item_id: int):
    """
    AJAX: run a single job item immediately.

    Called by the live-progress UI to run items one at a time so the
    frontend can update each row's status badge as they complete.
    """
    from flask import current_app
    from app.admin.models import SyncJobItem
    from app.core.epicor_client import KineticClient
    from app.core.epicor_importers import REGISTRY
    from app.core.scheduler import _resolve_item_params

    item = SyncJobItem.query.filter_by(id=item_id, job_id=job_id).first_or_404()
    key  = item.importer_key

    if key not in REGISTRY:
        return jsonify({"status": "error", "message": f"Unknown importer: {key!r}"}), 400

    try:
        with KineticClient.from_app(current_app._get_current_object()) as client:
            batch = REGISTRY[key](client).run(
                triggered_by_id=current_user.id,
                params=_resolve_item_params(item),
            )
        db.session.add(item)
        item.last_status    = SyncJobItem.STATUS_SUCCESS
        item.last_row_count = batch.row_count
        item.last_error     = None
        item.last_run_at    = datetime.now(timezone.utc)
        db.session.commit()
        return jsonify({
            "status":        "ok",
            "row_count":     batch.row_count,
            "rows_inserted": batch.rows_inserted,
        })
    except Exception as exc:
        db.session.add(item)
        item.last_status = SyncJobItem.STATUS_FAILED
        item.last_error  = str(exc)
        item.last_run_at = datetime.now(timezone.utc)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Admin Dashboard
# ---------------------------------------------------------------------------

@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    user_count = User.query.count()
    active_count = User.query.filter_by(is_active=True).count()
    role_count = Role.query.count()
    dept_count = Department.query.filter_by(is_active=True).count()
    recent_batches = (
        ImportBatch.query.order_by(ImportBatch.uploaded_at.desc()).limit(5).all()
    )
    recent_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(10).all()
    return render_template(
        "admin/dashboard.html",
        title="Admin Dashboard",
        user_count=user_count,
        active_count=active_count,
        role_count=role_count,
        dept_count=dept_count,
        recent_batches=recent_batches,
        recent_logs=recent_logs,
    )


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

@admin_bp.route("/users")
@login_required
@admin_required
def user_list():
    page = request.args.get("page", 1, type=int)
    users = User.query.order_by(User.username).paginate(page=page, per_page=25, error_out=False)
    return render_template("admin/user_list.html", title="Users", users=users)


@admin_bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@login_required
@admin_required
def user_detail(user_id: int):
    user = User.query.get_or_404(user_id)
    all_roles = Role.query.order_by(Role.name).all()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "toggle_active":
            user.is_active = not user.is_active
            db.session.commit()
            status = "activated" if user.is_active else "deactivated"
            flash(f"User {user.username} has been {status}.", "success")

        elif action == "assign_role":
            role_id = request.form.get("role_id", type=int)
            role = Role.query.get(role_id)
            if role and role not in user.roles:
                user.roles.append(role)
                db.session.commit()
                flash(f"Role '{role.name}' assigned to {user.username}.", "success")

        elif action == "revoke_role":
            role_id = request.form.get("role_id", type=int)
            role = Role.query.get(role_id)
            if role and role in user.roles:
                user.roles.remove(role)
                db.session.commit()
                flash(f"Role '{role.name}' revoked from {user.username}.", "warning")

        return redirect(url_for("admin.user_detail", user_id=user_id))

    return render_template(
        "admin/user_detail.html",
        title=f"User: {user.username}",
        user=user,
        all_roles=all_roles,
    )


# ---------------------------------------------------------------------------
# Role Management
# ---------------------------------------------------------------------------

@admin_bp.route("/users/create", methods=["GET", "POST"])
@login_required
@admin_required
def user_create():
    all_roles = Role.query.order_by(Role.name).all()

    if request.method == "POST":
        username   = request.form.get("username", "").strip()
        email      = request.form.get("email", "").strip()
        password   = request.form.get("password", "")
        first_name = request.form.get("first_name", "").strip() or None
        last_name  = request.form.get("last_name", "").strip() or None
        department = request.form.get("department", "").strip() or None
        role_ids   = request.form.getlist("role_ids", type=int)

        errors = []
        if not username:
            errors.append("Username is required.")
        elif User.query.filter_by(username=username).first():
            errors.append(f"Username '{username}' is already taken.")
        if not email:
            errors.append("Email is required.")
        elif User.query.filter_by(email=email).first():
            errors.append(f"Email '{email}' is already registered.")
        if not password:
            errors.append("Password is required.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "admin/user_create.html",
                title="Create User",
                all_roles=all_roles,
                form_data=request.form,
            )

        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            department=department,
            is_active=True,
        )
        user.set_password(password)
        for role in Role.query.filter(Role.id.in_(role_ids)).all():
            user.roles.append(role)
        db.session.add(user)
        db.session.commit()
        flash(f"User '{username}' created successfully.", "success")
        return redirect(url_for("admin.user_detail", user_id=user.id))

    return render_template(
        "admin/user_create.html",
        title="Create User",
        all_roles=all_roles,
        form_data={},
    )


@admin_bp.route("/roles")
@login_required
@admin_required
def role_list():
    roles = Role.query.order_by(Role.name).all()
    return render_template("admin/role_list.html", title="Roles & Permissions", roles=roles)


@admin_bp.route("/roles/create", methods=["GET", "POST"])
@login_required
@admin_required
def role_create():
    all_permissions = Permission.query.order_by(Permission.module, Permission.name).all()
    grouped_perms = {}
    for perm in all_permissions:
        grouped_perms.setdefault(perm.module or "other", []).append(perm)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        perm_ids = request.form.getlist("permission_ids", type=int)

        errors = []
        if not name:
            errors.append("Role name is required.")
        elif Role.query.filter_by(name=name).first():
            errors.append(f"Role '{name}' already exists.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "admin/role_create.html",
                title="Create Role",
                all_permissions=all_permissions,
                grouped_perms=grouped_perms,
                form_data=request.form,
            )

        role = Role(name=name, description=description)
        for perm in Permission.query.filter(Permission.id.in_(perm_ids)).all():
            role.permissions.append(perm)
        db.session.add(role)
        db.session.commit()
        flash(f"Role '{name}' created.", "success")
        return redirect(url_for("admin.role_detail", role_id=role.id))

    return render_template(
        "admin/role_create.html",
        title="Create Role",
        all_permissions=all_permissions,
        grouped_perms=grouped_perms,
        form_data=None,
    )


@admin_bp.route("/roles/<int:role_id>", methods=["GET", "POST"])
@login_required
@admin_required
def role_detail(role_id: int):
    role = Role.query.get_or_404(role_id)
    all_permissions = Permission.query.order_by(Permission.module, Permission.name).all()
    grouped_perms = {}
    for perm in all_permissions:
        grouped_perms.setdefault(perm.module or "other", []).append(perm)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_info":
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            if not name:
                flash("Role name is required.", "danger")
            elif name != role.name and Role.query.filter_by(name=name).first():
                flash(f"Role name '{name}' is already taken.", "danger")
            else:
                role.name = name
                role.description = description
                db.session.commit()
                flash("Role updated.", "success")

        elif action == "grant_permission":
            perm_id = request.form.get("permission_id", type=int)
            perm = Permission.query.get(perm_id)
            if perm and perm not in role.permissions:
                role.permissions.append(perm)
                db.session.commit()
                flash(f"Permission '{perm.name}' granted.", "success")

        elif action == "revoke_permission":
            perm_id = request.form.get("permission_id", type=int)
            perm = Permission.query.get(perm_id)
            if perm and perm in role.permissions:
                role.permissions.remove(perm)
                db.session.commit()
                flash(f"Permission '{perm.name}' revoked.", "warning")

        return redirect(url_for("admin.role_detail", role_id=role_id))

    return render_template(
        "admin/role_detail.html",
        title=f"Role: {role.name}",
        role=role,
        grouped_perms=grouped_perms,
    )


@admin_bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@login_required
@admin_required
def role_delete(role_id: int):
    role = Role.query.get_or_404(role_id)
    if role.name == "admin":
        flash("The admin role cannot be deleted.", "danger")
        return redirect(url_for("admin.role_list"))
    user_count = role.users.count()
    if user_count > 0:
        flash(f"Cannot delete '{role.name}' — it is assigned to {user_count} user(s). Revoke it first.", "danger")
        return redirect(url_for("admin.role_detail", role_id=role_id))
    db.session.delete(role)
    db.session.commit()
    flash(f"Role '{role.name}' deleted.", "warning")
    return redirect(url_for("admin.role_list"))


@admin_bp.route("/seed")
@login_required
@admin_required
def seed():
    """Seed default roles and permissions (idempotent)."""
    RoleService.seed_default_roles_and_permissions()
    flash("Default roles and permissions have been seeded.", "success")
    return redirect(url_for("admin.role_list"))


@admin_bp.route("/departments/seed")
@login_required
@admin_required
def seed_departments():
    """Redirect: departments are now created per-site via Admin → Departments."""
    flash("Departments are now site-scoped. Create them through Admin → Departments.", "info")
    return redirect(url_for("admin.dept_list"))


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

@admin_bp.route("/audit")
@login_required
@admin_required
def audit_log():
    from app.auth.models import User
    page       = request.args.get("page", 1, type=int)
    action_f   = request.args.get("action", "").strip()
    user_f     = request.args.get("user", "").strip()
    date_from  = request.args.get("date_from", "").strip()
    date_to    = request.args.get("date_to", "").strip()

    q = AuditLog.query

    if action_f:
        q = q.filter(AuditLog.action.ilike(f"%{action_f}%"))
    if user_f:
        user_ids = [u.id for u in User.query.filter(User.username.ilike(f"%{user_f}%")).all()]
        q = q.filter(AuditLog.user_id.in_(user_ids) if user_ids else db.false())
    if date_from:
        try:
            from datetime import date as _date
            q = q.filter(AuditLog.timestamp >= _date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import date as _date, timedelta
            q = q.filter(AuditLog.timestamp < _date.fromisoformat(date_to) + timedelta(days=1))
        except ValueError:
            pass

    logs = q.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=50, error_out=False)

    # Distinct action values for the dropdown
    actions = [r[0] for r in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]

    return render_template(
        "admin/audit_log.html",
        title="Audit Log",
        logs=logs,
        actions=actions,
        action_f=action_f,
        user_f=user_f,
        date_from=date_from,
        date_to=date_to,
    )


# ---------------------------------------------------------------------------
# Department Management
# ---------------------------------------------------------------------------

@admin_bp.route("/departments")
@login_required
@admin_required
def dept_list():
    departments = Department.query.order_by(
        Department.flow_order.asc().nullslast(), Department.name.asc()
    ).all()
    return render_template("admin/dept_list.html", title="Departments", departments=departments)


@admin_bp.route("/departments/add", methods=["GET", "POST"])
@login_required
@admin_required
def dept_add():
    form = DeptCreateForm()
    if request.method == "GET":
        form.track.data = True
    if form.validate_on_submit():
        code = form.code.data.strip().upper()
        if Department.query.filter_by(code=code).first():
            flash(f"A department with code '{code}' already exists.", "danger")
        else:
            dept = Department(
                code=code,
                name=form.name.data.strip(),
                target_hours_per_day=form.target_hours_per_day.data,
                flow_order=form.flow_order.data,
                op_code=form.op_code.data.strip().upper() or None,
                track=form.track.data,
                is_active=True,
            )
            db.session.add(dept)
            db.session.commit()
            flash(f"Department '{dept.name}' created.", "success")
            return redirect(url_for("admin.dept_list"))
    return render_template("admin/dept_add.html", title="Add Department", form=form)


@admin_bp.route("/departments/<int:dept_id>", methods=["GET", "POST"])
@login_required
@admin_required
def dept_edit(dept_id: int):
    dept = Department.query.get_or_404(dept_id)
    form = DeptHoursForm(obj=dept)

    if form.validate_on_submit():
        action = request.form.get("action")
        if action == "toggle_active":
            dept.is_active = not dept.is_active
            db.session.commit()
            status = "activated" if dept.is_active else "deactivated"
            flash(f"Department '{dept.name}' {status}.", "success")
        else:
            dept.target_hours_per_day = form.target_hours_per_day.data
            dept.flow_order = form.flow_order.data  # None clears it
            dept.op_code = form.op_code.data.strip().upper() or None
            dept.track = form.track.data
            db.session.commit()
            flash(f"Settings updated for {dept.name}.", "success")
        return redirect(url_for("admin.dept_list"))

    return render_template(
        "admin/dept_edit.html",
        title=f"Edit: {dept.name}",
        dept=dept,
        form=form,
    )


# ---------------------------------------------------------------------------
# CSV Import Management
# ---------------------------------------------------------------------------

@admin_bp.route("/imports")
@login_required
@permission_required("manage_imports")
def import_list():
    page = request.args.get("page", 1, type=int)
    import_type = request.args.get("type", "")
    q = ImportBatch.query.order_by(ImportBatch.uploaded_at.desc())
    if import_type:
        q = q.filter_by(import_type=import_type)
    batches = q.paginate(page=page, per_page=30, error_out=False)
    return render_template(
        "admin/import_list.html",
        title="Import History",
        batches=batches,
        import_type=import_type,
    )


@admin_bp.route("/imports/<int:batch_id>")
@login_required
@permission_required("manage_imports")
def import_detail(batch_id: int):
    batch = ImportBatch.query.get_or_404(batch_id)
    return render_template(
        "admin/import_detail.html",
        title=f"Import #{batch.id}",
        batch=batch,
    )


@admin_bp.route("/imports/upload", methods=["GET", "POST"])
@login_required
@permission_required("manage_imports")
def import_upload():
    form = ImportUploadForm()

    if form.validate_on_submit():
        import_type = form.import_type.data
        file_storage = form.file.data
        filename = file_storage.filename
        stream = io.BytesIO(file_storage.read())

        try:
            batch = _run_importer(import_type, stream, filename, current_user.id)
        except Exception as exc:
            flash(f"Import failed: {exc}", "danger")
            return redirect(url_for("admin.import_upload"))

        if batch.status == ImportBatch.STATUS_SUCCESS:
            flash(
                f"Import complete — {batch.rows_inserted} inserted, "
                f"{batch.rows_updated} updated"
                + (f", {batch.rows_closed} closed" if batch.rows_closed else "")
                + ".",
                "success",
            )
        else:
            flash(f"Import failed: {batch.error_message}", "danger")

        return redirect(url_for("admin.import_detail", batch_id=batch.id))

    return render_template("admin/import_upload.html", title="Upload CSV", form=form)


def _run_importer(import_type: str, stream, filename: str, user_id: int) -> ImportBatch:
    """Dispatch to the correct importer class."""
    from app.purchasing.materials.importers import (
        StockImporter, OpenPoImporter, MainMaterialImporter,
    )
    from app.planning.capacity.importers import LabourPlanImporter

    dispatch = {
        "stock":           StockImporter,
        "open_po":         OpenPoImporter,
        "main_material":   MainMaterialImporter,
        "labour_plan":     LabourPlanImporter,
    }
    importer_cls = dispatch[import_type]
    return importer_cls.import_file(stream, uploaded_by_id=user_id, filename=filename)


# ---------------------------------------------------------------------------
# ERP Data Viewers
# ---------------------------------------------------------------------------

@admin_bp.route("/data/main-material")
@login_required
@permission_required("view_materials")
def data_main_material():
    from app.purchasing.materials.models import MaterialRequirementMain
    q = request.args.get("q", "").strip()
    f_dept = request.args.get("dept", "").strip()
    page = request.args.get("page", 1, type=int)
    query = MaterialRequirementMain.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            MaterialRequirementMain.works_order.ilike(like),
            MaterialRequirementMain.material_code.ilike(like),
            MaterialRequirementMain.material_description.ilike(like),
        ))
    if f_dept:
        query = query.filter(MaterialRequirementMain.warehouse_code == f_dept)
    rows = query.order_by(MaterialRequirementMain.due_date, MaterialRequirementMain.works_order).paginate(page=page, per_page=50, error_out=False)
    total = MaterialRequirementMain.query.count()
    last = MaterialRequirementMain.query.order_by(MaterialRequirementMain.imported_at.desc()).first()
    from sqlalchemy import distinct
    depts = [r[0] for r in db.session.query(distinct(MaterialRequirementMain.warehouse_code)).filter(MaterialRequirementMain.warehouse_code.isnot(None)).order_by(MaterialRequirementMain.warehouse_code).all()]
    return render_template(
        "admin/data_main_material.html",
        title="Main Material Requirements",
        rows=rows, q=q, f_dept=f_dept, depts=depts, total=total,
        last_imported=last.imported_at if last else None,
    )


# ---------------------------------------------------------------------------
# System Settings
# ---------------------------------------------------------------------------

@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def system_settings():
    form = SystemSettingsForm()

    if form.validate_on_submit():
        SystemSetting.set_bool(
            SETTING_AUTO_COMPLETE_DESPATCH,
            form.auto_complete_despatch.data,
            description=(
                "Automatically mark Despatch as completed when all other "
                "operations for an order line are completed."
            ),
        )
        SystemSetting.set(
            SETTING_DAILY_OUTPUT_TARGET,
            str(form.daily_output_target.data or 0),
            description="Factory daily output target (units).",
        )
        day_map = [
            (0, form.daily_target_mon),
            (1, form.daily_target_tue),
            (2, form.daily_target_wed),
            (3, form.daily_target_thu),
            (4, form.daily_target_fri),
        ]
        target_days_str = ','.join(str(i) for i, f in day_map if f.data)
        SystemSetting.set(
            SETTING_DAILY_OUTPUT_TARGET_DAYS,
            target_days_str or '0,1,2,3',
            description="Weekdays on which the daily target applies (0=Mon, 4=Fri).",
        )
        SystemSetting.set(
            SETTING_MRP_LEAD_DAYS,
            str(form.mrp_lead_days.data if form.mrp_lead_days.data is not None else 14),
            description="Days before ship date that fabric/hide materials must arrive on PO to count as covered.",
        )
        SystemSetting.set(
            SETTING_MRP_COMPONENT_LEAD_DAYS,
            str(form.mrp_component_lead_days.data if form.mrp_component_lead_days.data is not None else 14),
            description="Days before ship date that component materials must arrive on PO to count as covered.",
        )
        SystemSetting.set(
            SETTING_FABRIC_CLASS_IDS,
            (form.fabric_class_ids.data or "").strip(),
            description="Epicor class IDs included in fabric/hide availability assessment (comma-separated).",
        )
        SystemSetting.set(
            SETTING_COMPONENT_CLASS_IDS,
            (form.component_class_ids.data or "").strip(),
            description="Epicor class IDs included in component availability assessment (comma-separated; blank = all).",
        )
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin.system_settings"))

    # Pre-populate form from current DB values
    form.auto_complete_despatch.data = SystemSetting.get_bool(
        SETTING_AUTO_COMPLETE_DESPATCH, default=False
    )
    form.daily_output_target.data = SystemSetting.get_int(
        SETTING_DAILY_OUTPUT_TARGET, default=128
    )
    form.mrp_lead_days.data = SystemSetting.get_int(
        SETTING_MRP_LEAD_DAYS, default=14
    )
    form.mrp_component_lead_days.data = SystemSetting.get_int(
        SETTING_MRP_COMPONENT_LEAD_DAYS, default=14
    )
    form.fabric_class_ids.data = SystemSetting.get(
        SETTING_FABRIC_CLASS_IDS, "A101,A102,A105,B101,C101,Z102"
    )
    form.component_class_ids.data = SystemSetting.get(
        SETTING_COMPONENT_CLASS_IDS, ""
    )
    _tdays = set(
        int(d) for d in
        SystemSetting.get(SETTING_DAILY_OUTPUT_TARGET_DAYS, '0,1,2,3').split(',')
        if d.strip().isdigit()
    )
    form.daily_target_mon.data = 0 in _tdays
    form.daily_target_tue.data = 1 in _tdays
    form.daily_target_wed.data = 2 in _tdays
    form.daily_target_thu.data = 3 in _tdays
    form.daily_target_fri.data = 4 in _tdays

    return render_template(
        "admin/settings.html",
        title="System Settings",
        form=form,
    )
