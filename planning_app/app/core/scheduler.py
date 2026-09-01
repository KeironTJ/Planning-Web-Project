"""
Background scheduler for automated Epicor data sync.

A single 60-second tick job checks the sync_jobs table and fires any jobs
whose next_run_at has passed.  Each job runs its SyncJobItems in sort_order
sequence, sharing a single KineticClient session.

Design notes:
- One tick job; schedule config lives in the DB (not in APScheduler jobs).
- is_running flag prevents double-execution whether triggered by the
  scheduler or a manual "Run now" from the UI.
- Manual runs spawn their own daemon thread directly — no queuing, no
  dependency on the scheduler thread being active.
- The scheduler is NOT started during testing (TESTING=True config).
- With Flask's debug reloader, WERKZEUG_RUN_MAIN guard ensures we only
  start one scheduler instance.
"""

from __future__ import annotations

import logging
import threading
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


def _execute_job(app, job_id: int) -> None:
    """
    Run a SyncJob by ID inside an app context.  Assumes is_running is already
    set to True by the caller (route or scheduler tick).  Runs all items and
    clears is_running when done.  Safe to call from any thread.
    """
    import os

    from app.admin.models import SyncJob, SyncJobItem
    from app.core.epicor_client import KineticClient
    from app.core.epicor_importers import REGISTRY
    from app.extensions import db

    with app.app_context():
        job = SyncJob.query.get(job_id)
        if job is None:
            logger.warning("_execute_job: job %d not found", job_id)
            return

        logger.info("_execute_job: starting job %d %r (%d items) pid=%d",
                    job.id, job.name, len(job.items), os.getpid())
        item_statuses: list[str] = []

        try:
            with KineticClient.from_app(app) as client:
                for item in job.items:
                    key = item.importer_key
                    if key not in REGISTRY:
                        logger.warning("_execute_job: unknown importer key %r in job %d — skipping", key, job.id)
                        continue

                    try:
                        batch = REGISTRY[key](client).run(params=_resolve_item_params(item))
                        db.session.add(item)
                        item.last_status    = SyncJobItem.STATUS_SUCCESS
                        item.last_row_count = batch.row_count
                        item.last_error     = None
                        item_statuses.append("success")
                        logger.info("_execute_job: job %d item %r → %d rows", job.id, key, batch.row_count)
                    except Exception as exc:
                        db.session.add(item)
                        item.last_status = SyncJobItem.STATUS_FAILED
                        item.last_error  = str(exc)
                        item_statuses.append("failed")
                        logger.exception("_execute_job: job %d item %r failed: %s", job.id, key, exc)
                    finally:
                        item.last_run_at = datetime.now(timezone.utc)
                        try:
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                            logger.exception("_execute_job: failed to save item result for %r in job %d", key, job.id)

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
            logger.exception("_execute_job: job %d %r crashed: %s", job.id, job.name, exc)
        finally:
            db.session.add(job)
            job.is_running  = False
            job.last_run_at = datetime.now(timezone.utc)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                logger.exception("_execute_job: failed to save results for job %d", job.id)


def run_job_in_thread(app, job_id: int) -> None:
    """
    Spawn a daemon thread to run a single job.  Called after the route has
    already set is_running=True and committed, so polls see the running state
    immediately without any race window.
    """
    t = threading.Thread(
        target=_execute_job,
        args=(app, job_id),
        daemon=True,
        name=f"epicor-manual-job-{job_id}",
    )
    t.start()
    logger.info("run_job_in_thread: spawned thread for job %d", job_id)


def run_due_jobs(app) -> None:
    """
    Check the DB for enabled SyncJobs that are past their next_run_at,
    claim each one (is_running=True), then execute them.
    Called every 60 seconds by the scheduler tick.
    """
    import os
    from datetime import timedelta

    logger.info("Scheduler tick fired (pid=%d)", os.getpid())

    try:
        from app.admin.models import SyncJob
        from app.extensions import db

        with app.app_context():
            now = datetime.now(timezone.utc)

            # --- Stuck-job watchdog -------------------------------------------
            # A job is only considered stuck if it has been marked is_running
            # for more than 1 hour (last_run_at is the start-time proxy).
            # We must NOT reset jobs that are legitimately running but whose
            # next_run_at has already passed (e.g. short-interval jobs).
            stuck_jobs = (
                SyncJob.query
                .filter(SyncJob.is_running == True)    # noqa: E712
                .filter(
                    db.or_(
                        SyncJob.last_run_at <= now - timedelta(hours=1),
                        db.and_(
                            SyncJob.last_run_at == None,   # noqa: E711
                            SyncJob.next_run_at <= now - timedelta(hours=1),
                        ),
                    )
                )
                .all()
            )
            if stuck_jobs:
                for stuck in stuck_jobs:
                    logger.warning(
                        "Scheduler: resetting stuck job %d %r (is_running=True, next_run_at=%s)",
                        stuck.id, stuck.name,
                        stuck.next_run_at.strftime("%H:%M:%S") if stuck.next_run_at else "None",
                    )
                    stuck.is_running  = False
                    stuck.last_status = SyncJob.STATUS_FAILED
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.exception("Scheduler: failed to reset stuck jobs")
            # -----------------------------------------------------------------

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

            enabled_total = SyncJob.query.filter(SyncJob.enabled == True).count()  # noqa: E712
            logger.info(
                "Scheduler tick: %d due (of %d enabled) — now=%s",
                len(due_jobs), enabled_total, now.strftime("%H:%M:%S"),
            )

            # Claim each job now (before leaving the app context) so the
            # is_running flag is committed before execution begins.
            claimed_ids = []
            for job in due_jobs:
                job.is_running  = True
                job.next_run_at = now + timedelta(minutes=job.interval_minutes)
                try:
                    db.session.commit()
                    claimed_ids.append(job.id)
                except Exception:
                    db.session.rollback()
                    logger.exception("Scheduler: failed to claim job %d %r", job.id, job.name)

    except Exception:
        logger.exception("Scheduler tick: unhandled exception in run_due_jobs")
        return

    # Spawn each job in its own daemon thread so the tick thread is never
    # blocked.  If a job hangs (e.g. Epicor network timeout), the tick loop
    # continues firing every 60 s and can still pick up other due jobs.
    for job_id in claimed_ids:
        t = threading.Thread(
            target=_execute_job,
            args=(app, job_id),
            daemon=True,
            name=f"epicor-sched-job-{job_id}",
        )
        t.start()
        logger.info("Scheduler: spawned thread for job %d", job_id)


def init_scheduler(app) -> None:
    """
    Start a daemon thread that calls run_due_jobs every 60 seconds.

    The first tick fires immediately on startup so due jobs are not delayed
    by a full minute after a restart.  Subsequent ticks run every 60 seconds.

    Manual "Run now" runs bypass this thread entirely — they spawn their own
    thread via run_job_in_thread() so they work even in dev where this
    scheduler may not be active.

    Called from the app factory.  Skipped in TESTING mode and during non-server
    Flask CLI commands (flask db upgrade, flask shell, etc.).
    Always starts when serving HTTP (flask run, gunicorn, wsgi.py, VS Code debugger).
    """
    import os
    import time

    if app.config.get("TESTING"):
        logger.info("Scheduler: skipped (TESTING=True)")
        return

    # Skip for non-server Flask CLI commands (flask db upgrade, flask shell, etc.)
    # but NOT for 'flask run' which is the dev server.
    # The daemon thread cleanup on exit of short-lived CLI commands causes a
    # segfault on Python 3.13, so we must guard against those.
    try:
        import click
        ctx = click.get_current_context(silent=True)
        if ctx is not None:
            # Walk the context chain to find the command name.
            # 'flask run' will have a context with command.name == 'run'.
            is_server_cmd = False
            c = ctx
            while c is not None:
                if getattr(c.command, 'name', None) == 'run':
                    is_server_cmd = True
                    break
                c = c.parent
            if not is_server_cmd:
                cmd_name = getattr(ctx.command, 'name', '?')
                logger.info("Scheduler: skipped (Flask CLI command: %r)", cmd_name)
                return
    except (ImportError, AttributeError):
        pass

    # In debug mode Flask's reloader runs two processes.  We used to restrict
    # the scheduler to the child process (WERKZEUG_RUN_MAIN=true) but that
    # breaks VS Code's debugger which never sets that env var.  The is_running
    # DB flag is sufficient to prevent double-execution if both processes
    # happen to start a scheduler thread simultaneously.

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
            try:
                run_due_jobs(app)
            except Exception:
                logger.exception("Scheduler: tick crashed — will retry next cycle")
            time.sleep(60)

    _reset_stuck_jobs_at_startup()

    t = threading.Thread(target=_tick_loop, daemon=True, name="epicor-sync-tick")
    t.start()
    logger.info(
        "Epicor sync scheduler started (tick every 60 s) — pid=%d debug=%s",
        os.getpid(), app.debug,
    )
