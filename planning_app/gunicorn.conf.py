"""
Gunicorn configuration for the Planning Hub.

Usage (from the planning_app directory):
    gunicorn -c gunicorn.conf.py wsgi:app
"""

import os

bind         = "0.0.0.0:8000"
workers      = 4
worker_class = "sync"
timeout      = 300

# Load the .env file before workers boot so FLASK_ENV etc. are visible.
# python-dotenv is already a project dependency.
def on_starting(server):
    from dotenv import load_dotenv
    load_dotenv()

# Only start the background scheduler in the first worker (age==1) so there
# is exactly one scheduler instance regardless of worker count.
def post_fork(server, worker):
    if worker.age == 1:
        from wsgi import app
        from app.core.scheduler import init_scheduler
        init_scheduler(app)
        server.log.info("Scheduler started in worker pid=%d", os.getpid())
