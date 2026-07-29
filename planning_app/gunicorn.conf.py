"""
Gunicorn configuration for the Planning Hub.

Usage (from the planning_app directory):
    gunicorn -c gunicorn.conf.py wsgi:app
"""

import os

bind         = "0.0.0.0:8000"
# Multiple workers for concurrent request handling.
# The scheduler runs in only ONE worker (the first to start); the
# WERKZEUG_RUN_MAIN guard in init_scheduler handles debug-mode deduplication,
# and the is_running DB flag prevents double-execution across workers.
workers      = 4
worker_class = "sync"
timeout      = 300


def post_fork(server, worker):
    """Tag each worker with a sequential ID so the scheduler only starts in worker 1."""
    os.environ["WORKER_ID"] = str(worker.age)
