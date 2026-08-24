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

            # --- Stuck-job watchdog -------------------------------------------
            # If a job has is_running=True but its next_run_at has already
            # passed, it has been "running" longer than its own interval and
            # is almost certainly stuck (process restart, unhandled crash, etc.).
            stuck_jobs = (
                SyncJob.query
                .filter(
                    SyncJob.is_running == True,    # noqa: E712
                    SyncJob.next_run_at <= now,
                )
                .all()
            )
            if stuck_jobs:
                for stuck in stuck_jobs:
                    logger.warning(
                        "Scheduler: resetting stuck job %d %r "
                        "(is_running=True past next_run_at %s)",
                        stuck.id, stuck.name,
                        stuck.next_run_at.strftime("%H:%M:%S") if stuck.next_run_at else "None",
                    )
                    stuck.is_running = False
                    stuck.last_status = SyncJob.STATUS_FAILED
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.exception("Scheduler: failed to reset stuck jobs")
            # -----------------------------------------------------------------

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
                                db.session.add(item)
                                item.last_status    = SyncJobItem.STATUS_SUCCESS
                                item.last_row_count = batch.row_count
                                item.last_error     = None
                                item_statuses.append("success")
                                logger.info("Scheduler: job %d item %r → %d rows", job.id, key, batch.row_count)
                            except Exception as exc:
                                db.session.add(item)
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
                    db.session.add(job)
                    if not item_statuses or all(s == "success" for s in item_statuses):
                        job.last_status = SyncJob.STATUS_SUCCESS
                    elif all(s == "failed" for s in item_statuses):
                        job.last_status = SyncJob.STATUS_FAILED
                    else:
                        job.last_status = SyncJob.STATUS_PARTIAL

                except Exception as exc:
                    db.session.add(job)
                    job.last_status = SyncJob.STATUS_FAILED
                    logger.exception("Scheduler: job %d %r crashed: %s", job.id, job.name, exc)
                finally:
                    db.session.add(job)
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
    Start a daemon thread that calls run_due_jobs every 60 seconds.

    Called from the app factory.  Skipped in TESTING mode and in the
    Werkzeug parent reloader process.
    """
    import os
    import threading
    import time

    if app.config.get("TESTING"):
        logger.info("Scheduler: skipped (TESTING=True)")
        return

    # Skip when running Flask CLI commands (flask db upgrade, flask shell, etc.).
    # CLI commands invoke create_app() but don't need a background scheduler,
    # and the daemon thread cleanup on exit causes a segfault on Python 3.13.
    try:
        import click
        if click.get_current_context(silent=True) is not None:
            logger.info("Scheduler: skipped (Flask CLI command)")
            return
    except ImportError:
        pass

    # In debug mode Flask runs two processes; only start in the child.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        logger.info("Scheduler: skipped (Werkzeug reloader parent process)")
        return

    # With multiple gunicorn workers each worker starts its own scheduler thread.
    # The is_running DB flag prevents double-execution, but wastes resources.
    # Restrict the scheduler to worker 1 only (WORKER_ID is set in gunicorn.conf.py).
    worker_id = os.environ.get("WORKER_ID")
    if worker_id is not None and worker_id != "1":
        logger.info("Scheduler: skipped (worker %s — only worker 1 runs the scheduler)", worker_id)
        return

    def _reset_stuck_jobs_at_startup():
        """Reset is_running flags left over from a previous crash or restart."""
        try:
            from app.admin.models import SyncJob
            from app.extensions import db
            with app.app_context():
                stuck = SyncJob.query.filter(SyncJob.is_running == True).all()  # noqa: E712
                if stuck:
                    for job in stuck:
                        logger.warning(
                            "Scheduler startup: resetting stuck job %d %r (was is_running=True)",
                            job.id, job.name,
                        )
                        job.is_running = False
                        job.last_status = SyncJob.STATUS_FAILED
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                        logger.exception("Scheduler startup: failed to reset stuck jobs")
        except Exception:
            logger.exception("Scheduler startup: error during stuck-job reset")

    def _tick_loop():
        logger.info("Scheduler thread started (pid=%d)", os.getpid())
        while True:
            time.sleep(60)
            try:
                run_due_jobs(app)
            except Exception:
                logger.exception("Scheduler: tick crashed — will retry next cycle")

    _reset_stuck_jobs_at_startup()

    t = threading.Thread(target=_tick_loop, daemon=True, name="epicor-sync-tick")
    t.start()
    logger.info(
        "Epicor sync scheduler started (tick every 60 s) — pid=%d debug=%s",
        os.getpid(), app.debug,
    )
