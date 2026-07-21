"""
Background scheduler for automated Epicor data sync.

A single 60-second tick job checks the sync_jobs table and fires any jobs
whose next_run_at has passed.  Each job runs its SyncJobItems in sort_order
sequence, sharing a single KineticClient session.

Design notes:
- One tick job; schedule config lives in the DB (not in APScheduler jobs).
- is_running flag prevents double-execution if a job takes > 60 s.
- The scheduler is NOT started during testing (TESTING=True config).
- With Flask's debug reloader, WERKZEUG_RUN_MAIN guard ensures we only
  start one scheduler instance.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _resolve_item_params(item) -> dict | None:
    """
    Return runtime params for a SyncJobItem, handling the sales_closed
    UK-format date conversion.
    """
    from datetime import date as _date

    params = item.resolved_params()
    if not params:
        return None

    # sales_closed BAQ expects UK-format dates (dd/mm/yyyy);
    # we store them as ISO (YYYY-MM-DD) for the date picker.
    if item.importer_key == "sales_closed":
        def _to_uk(iso_str):
            try:
                return _date.fromisoformat(iso_str).strftime("%d/%m/%Y")
            except (ValueError, TypeError):
                return iso_str
        if "OrderDateFrom" in params:
            params["OrderDateFrom"] = _to_uk(params["OrderDateFrom"])
        if "OrderDateTo" in params:
            params["OrderDateTo"] = _to_uk(params["OrderDateTo"])

    return params


def run_due_jobs(app) -> None:
    """
    Check the DB for enabled SyncJobs that are past their next_run_at and
    execute them in item order.  Called every 60 seconds by the scheduler.

    Args:
        app: The Flask application instance (not the proxy).
    """
    import os
    from datetime import timedelta

    # Log BEFORE entering the app context so we know APScheduler called us.
    logger.info("Scheduler tick fired (pid=%d)", os.getpid())

    try:
        from app.admin.models import SyncJob, SyncJobItem
        from app.core.epicor_client import KineticClient
        from app.core.epicor_importers import REGISTRY
        from app.extensions import db

        with app.app_context():
            now = datetime.now(timezone.utc)

            # Jobs with NULL next_run_at were never scheduled; treat them as
            # immediately due so they run on the first tick after being enabled.
            due_jobs = (
                SyncJob.query
                .filter(
                    SyncJob.enabled == True,       # noqa: E712
                    SyncJob.is_running == False,   # noqa: E712
                )
                .filter(
                    db.or_(
                        SyncJob.next_run_at == None,   # noqa: E711
                        SyncJob.next_run_at <= now,
                    )
                )
                .all()
            )

            # Always log the tick so it's visible in production logs.
            enabled_total = SyncJob.query.filter(SyncJob.enabled == True).count()  # noqa: E712
            logger.info(
                "Scheduler tick: %d due (of %d enabled) — now=%s",
                len(due_jobs), enabled_total, now.strftime("%H:%M:%S"),
            )

            if not due_jobs:
                return

            for job in due_jobs:
                # Mark as running and push next_run_at forward immediately.
                job.is_running  = True
                job.next_run_at = now + timedelta(minutes=job.interval_minutes)
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.exception("Scheduler: failed to lock job %d %r", job.id, job.name)
                    continue

                logger.info("Scheduler: starting job %d %r (%d items)", job.id, job.name, len(job.items))
                item_statuses: list[str] = []

                try:
                    with KineticClient.from_app(app) as client:
                        for item in job.items:
                            key = item.importer_key
                            if key not in REGISTRY:
                                logger.warning("Scheduler: unknown importer key %r in job %d — skipping", key, job.id)
                                continue

                            try:
                                batch = REGISTRY[key](client).run(params=_resolve_item_params(item))
                                item.last_status    = SyncJobItem.STATUS_SUCCESS
                                item.last_row_count = batch.row_count
                                item.last_error     = None
                                item_statuses.append("success")
                                logger.info("Scheduler: job %d item %r → %d rows", job.id, key, batch.row_count)
                            except Exception as exc:
                                item.last_status = SyncJobItem.STATUS_FAILED
                                item.last_error  = str(exc)
                                item_statuses.append("failed")
                                logger.exception("Scheduler: job %d item %r failed: %s", job.id, key, exc)
                            finally:
                                item.last_run_at = datetime.now(timezone.utc)
                                # Commit each item result immediately so the SQLite write
                                # lock is released before the next Epicor API call starts.
                                try:
                                    db.session.commit()
                                except Exception:
                                    db.session.rollback()
                                    logger.exception("Scheduler: failed to save item result for %r in job %d", key, job.id)

                    # Derive overall job status from items
                    if not item_statuses or all(s == "success" for s in item_statuses):
                        job.last_status = SyncJob.STATUS_SUCCESS
                    elif all(s == "failed" for s in item_statuses):
                        job.last_status = SyncJob.STATUS_FAILED
                    else:
                        job.last_status = SyncJob.STATUS_PARTIAL

                except Exception as exc:
                    job.last_status = SyncJob.STATUS_FAILED
                    logger.exception("Scheduler: job %d %r crashed: %s", job.id, job.name, exc)
                finally:
                    job.is_running  = False
                    job.last_run_at = datetime.now(timezone.utc)
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                        logger.exception("Scheduler: failed to save results for job %d", job.id)

    except Exception:
        logger.exception("Scheduler tick: unhandled exception in run_due_jobs")


def init_scheduler(app) -> None:
    """
    Attach the APScheduler tick job to the Flask app and start it.

    Called from the app factory.  Skipped in TESTING mode and in the
    Werkzeug parent reloader process.
    """
    import os

    from flask_apscheduler import APScheduler

    if app.config.get("TESTING"):
        logger.info("Scheduler: skipped (TESTING=True)")
        return

    # In debug mode Flask runs two processes; only start in the child.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        logger.info("Scheduler: skipped (Werkzeug reloader parent process)")
        return

    scheduler = APScheduler()
    scheduler.init_app(app)

    scheduler.add_job(
        id="epicor_sync_tick",
        func=run_due_jobs,
        args=[app],
        trigger="interval",
        seconds=60,
        misfire_grace_time=30,
        replace_existing=True,
    )

    scheduler.start()
    jobs = scheduler.get_jobs()
    if jobs:
        job_obj = jobs[0]
        next_run = getattr(job_obj, 'next_run_time', None) or getattr(job_obj, 'next_fire_time', None)
    else:
        next_run = "(no jobs)"
    logger.info(
        "Epicor sync scheduler started — pid=%d debug=%s next_tick=%s",
        os.getpid(), app.debug, next_run,
    )
