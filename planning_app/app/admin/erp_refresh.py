"""
ERP data import coordination.

The previous InterBase/ODBC direct-query integration has been removed.
Data is now loaded exclusively via CSV file uploads through the Admin → Import
interface.  This module retains the task-tracking infrastructure so the UI
polling contract remains intact if a future scheduled import is added.
"""

import copy
import threading
import uuid
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# In-memory task store
# ---------------------------------------------------------------------------

_tasks: dict = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_refresh(app, user_id: int) -> str:
    """
    Placeholder — direct ERP queries are no longer supported.

    Returns a task_id that immediately resolves to a 'not_supported' status
    so any legacy callers receive a graceful response instead of an error.
    """
    task_id = str(uuid.uuid4())
    with _lock:
        _tasks[task_id] = {
            "status": "not_supported",
            "steps": [
                {
                    "label": "Direct ERP refresh",
                    "status": "failed",
                    "message": (
                        "Direct ERP refresh is not available. "
                        "Please upload CSV exports via Admin \u2192 Imports."
                    ),
                }
            ],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    return task_id


def get_task(task_id: str) -> dict | None:
    """Return a deep copy of task state, or None if not found."""
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return None
        return copy.deepcopy(task)
