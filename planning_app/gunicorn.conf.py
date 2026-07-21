"""
Gunicorn configuration for the Planning Hub.

Usage (from the planning_app directory):
    gunicorn -c gunicorn.conf.py wsgi:app
"""

import os

bind         = "0.0.0.0:8000"
# Single worker so only one scheduler instance runs.
# For a staging server this is sufficient; move to PostgreSQL + multiple
# workers if you need higher HTTP concurrency in future.
workers      = 1
worker_class = "sync"
timeout      = 300
