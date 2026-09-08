'''Business logic for the Admin portal.'''

"""
Admin blueprint routes.

All routes here require the "admin" role.  The admin_required decorator
from core.decorators enforces this at the HTTP layer.
"""

from datetime import datetime, timezone

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import current_user


from app.extensions import db
from app.sales.orders.models import ImportBatch


# ---------------------------------------------------------------------------
# Epicor Data Sync
# ---------------------------------------------------------------------------


def handle_epicor_sync():
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


def handle_epicor_sync_run():
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


def handle_epicor_sync_run_one():
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
                    date_info = f" Â· {params['DateFrom']} â†’ {params.get('DateTo', '')}"
                elif baq_key == "sales_closed" and params.get("OrderDateFrom"):
                    date_info = f" Â· {params['OrderDateFrom']} â†’ {params.get('OrderDateTo', '')}"
                flash(
                    f"{baq_key} sync complete{date_info}"
                    f" Â· {batch.row_count} fetched, {batch.rows_inserted} inserted"
                    + (f" Â· {batch.notes}" if batch.notes else ""),
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
        flash(f"{baq_key}: DB locked after retries â€” {last_exc}", "danger")
        return jsonify({"status": "error", "key": baq_key,
                        "message": f"DB locked after retries: {last_exc}"}), 500
    except Exception as exc:
        flash(f"{baq_key} sync failed: {exc}", "danger")
        return jsonify({"status": "error", "key": baq_key, "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Sync Jobs  (job-based grouped schedules)
# ---------------------------------------------------------------------------

def handle_sync_schedules():
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


def handle_sync_job_create():
    """Create a new sync job (traditional form POST â†’ redirect)."""
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


def handle_sync_job_update(job_id: int):
    """
    AJAX: update job fields (name, enabled, interval_minutes).

    Expects JSON: {"name": "...", "enabled": true, "interval_minutes": 60}
    """
    from app.admin.models import SyncJob

    job = SyncJob.query.get_or_404(job_id)
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


def handle_sync_job_delete(job_id: int):
    """AJAX: delete a job and all its items."""
    from app.admin.models import SyncJob

    job = SyncJob.query.get_or_404(job_id)
    db.session.delete(job)
    db.session.commit()
    return jsonify({"status": "ok"})


def handle_sync_job_run_now(job_id: int):
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


def handle_sync_job_status(job_id: int):
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


def handle_sync_schedules_status():
    """AJAX: lightweight bulk status for all jobs â€” used by the 30-second
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


def handle_sync_job_item_add(job_id: int):
    """AJAX: add an importer to a job."""
    from app.admin.models import SyncJob, SyncJobItem
    from app.core.epicor_importers import REGISTRY

    SyncJob.query.get_or_404(job_id)
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


def handle_sync_job_item_update(job_id: int, item_id: int):
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


def handle_sync_job_item_delete(job_id: int, item_id: int):
    """AJAX: remove an item from a job."""
    from app.admin.models import SyncJobItem

    item = SyncJobItem.query.filter_by(id=item_id, job_id=job_id).first_or_404()
    key = item.importer_key
    db.session.delete(item)
    db.session.commit()
    return jsonify({"status": "ok", "importer_key": key})


def handle_sync_job_item_run_one(job_id: int, item_id: int):
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
