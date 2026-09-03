from datetime import datetime, timedelta, timezone

from app.admin.models import SyncJob
from app.core import scheduler


class _DeferredThread:
    created = []

    def __init__(self, *, target, args=(), daemon=None, name=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        self.started = False
        self.created.append(self)

    def start(self):
        self.started = True


def test_init_scheduler_starts_in_replacement_gunicorn_worker(
    app, monkeypatch
):
    monkeypatch.setenv("WORKER_ID", "5")
    monkeypatch.setitem(app.config, "TESTING", False)
    monkeypatch.setattr(scheduler.threading, "Thread", _DeferredThread)
    _DeferredThread.created.clear()

    scheduler.init_scheduler(app)

    assert len(_DeferredThread.created) == 1
    thread = _DeferredThread.created[0]
    assert thread.name == "epicor-sync-tick"
    assert thread.daemon is True
    assert thread.started is True


def test_run_due_jobs_claims_job_only_once(app, db_session, monkeypatch):
    due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    job = SyncJob(
        name="Atomic claim",
        enabled=True,
        interval_minutes=30,
        next_run_at=due_at,
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    monkeypatch.setattr(scheduler.threading, "Thread", _DeferredThread)
    _DeferredThread.created.clear()

    scheduler.run_due_jobs(app)
    scheduler.run_due_jobs(app)

    assert [thread.args[1] for thread in _DeferredThread.created] == [job_id]
    db_session.expire_all()
    claimed_job = db_session.get(SyncJob, job_id)
    assert claimed_job.is_running is True
    assert claimed_job.last_run_at is not None
    assert claimed_job.next_run_at > due_at.replace(tzinfo=None)


def test_stuck_job_is_reset_and_reclaimed_immediately(
    app, db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    job = SyncJob(
        name="Stuck job",
        enabled=True,
        interval_minutes=30,
        is_running=True,
        last_run_at=now - timedelta(hours=2),
        next_run_at=now + timedelta(hours=3),
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    monkeypatch.setattr(scheduler.threading, "Thread", _DeferredThread)
    _DeferredThread.created.clear()

    scheduler.run_due_jobs(app)

    assert [thread.args[1] for thread in _DeferredThread.created] == [job_id]


def test_init_scheduler_resets_orphaned_running_job_without_waiting_an_hour(
    app, db_session, monkeypatch
):
    """A crash/restart kills every thread, so a job left is_running=True only
    seconds ago (not yet past the 1h watchdog threshold) must still be reset
    immediately at startup — otherwise it would wrongly sit "running" for up
    to an hour before the periodic watchdog notices it."""
    now = datetime.now(timezone.utc)
    job = SyncJob(
        name="Orphaned by restart",
        enabled=True,
        interval_minutes=30,
        is_running=True,
        last_run_at=now - timedelta(minutes=1),
        next_run_at=now + timedelta(minutes=29),
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    monkeypatch.setenv("WORKER_ID", "1")
    monkeypatch.setitem(app.config, "TESTING", False)
    monkeypatch.setattr(scheduler.threading, "Thread", _DeferredThread)
    _DeferredThread.created.clear()

    scheduler.init_scheduler(app)

    db_session.expire_all()
    reset_job = db_session.get(SyncJob, job_id)
    assert reset_job.is_running is False
    assert reset_job.last_status == SyncJob.STATUS_FAILED


def test_reset_stuck_jobs_does_not_clobber_a_job_that_already_finished(
    app, db_session
):
    """The watchdog UPDATE's WHERE clause must re-check is_running, so a job
    that legitimately finished (is_running=False) between being read as
    'stuck' and any write is never overwritten back to FAILED."""
    now = datetime.now(timezone.utc)
    job = SyncJob(
        name="Finished just in time",
        enabled=True,
        interval_minutes=30,
        is_running=False,
        last_status=SyncJob.STATUS_SUCCESS,
        last_run_at=now - timedelta(hours=2),
        next_run_at=now + timedelta(minutes=30),
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    from app.extensions import db as _db

    with app.app_context():
        count = scheduler._reset_stuck_jobs(_db, SyncJob, now)
        _db.session.commit()

    assert count == 0
    db_session.expire_all()
    untouched = db_session.get(SyncJob, job_id)
    assert untouched.last_status == SyncJob.STATUS_SUCCESS


def test_scheduler_cycle_survives_tick_exit(app, monkeypatch):
    sleep_calls = []

    def exit_tick(_):
        raise SystemExit("simulated ticker crash")

    monkeypatch.setattr(scheduler, "run_due_jobs", exit_tick)
    monkeypatch.setattr(scheduler.time, "sleep", sleep_calls.append)

    scheduler._run_scheduler_cycle(app)

    assert sleep_calls == [60]
