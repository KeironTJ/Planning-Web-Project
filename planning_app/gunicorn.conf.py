"""
Gunicorn configuration for the Factory Dashboards.

Usage (from the planning_app directory):
    gunicorn -c gunicorn.conf.py wsgi:app
"""

import os

bind         = "0.0.0.0:8000"
# Multiple workers for concurrent request handling. Each worker runs a scheduler
# ticker for automatic failover; jobs are claimed atomically in the database.
workers      = 4
worker_class = "sync"
timeout      = 300


def post_fork(server, worker):
    """Tag each worker with a sequential ID for scheduler diagnostics."""
    os.environ["WORKER_ID"] = str(worker.age)
