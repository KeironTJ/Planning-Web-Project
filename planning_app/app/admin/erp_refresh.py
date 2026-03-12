"""
ERP data refresh — queries InterBase directly via pyodbc, builds an in-memory
CSV stream for each dataset, and feeds it straight into the planning importer.

No files are written to disk. Runs in a background daemon thread; progress is
stored in a module-level dict so any request can poll for status.
"""

import copy
import csv
import io
import os
import threading
import traceback
import uuid
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# In-memory task store
# ---------------------------------------------------------------------------

_tasks: dict = {}
_lock = threading.Lock()

# Queries live alongside this file in app/admin/queries/
_QUERIES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queries")

# ---------------------------------------------------------------------------
# Dataset definitions
# Step 0 = Connect; steps 1-N = one per dataset (query + import combined)
# ---------------------------------------------------------------------------

_DATASETS = [
    {"label": "Open Order Book",                  "query": "OpenOrderBook.sql",  "import_type": "oob"},
    {"label": "Stock on Hand",                    "query": "StockOnHand.sql",    "import_type": "stock"},
    {"label": "Open Purchase Orders",             "query": "OpenPO.sql",         "import_type": "open_po"},
    {"label": "Main Material Requirements",       "query": "MainMaterialReq.sql","import_type": "main_material"},
    {"label": "AfterSales Material Requirements", "query": "ASMaterialReq.sql",  "import_type": "as_material"},
]


def _make_steps() -> list:
    steps = [{"label": "Connect to ERP", "status": "pending", "message": ""}]
    for ds in _DATASETS:
        steps.append({"label": ds["label"], "status": "pending", "message": ""})
    return steps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_refresh(app, user_id: int) -> str:
    """Start a background ERP refresh. Returns the task_id."""
    task_id = str(uuid.uuid4())
    with _lock:
        _tasks[task_id] = {
            "status": "running",
            "steps": _make_steps(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }
    thread = threading.Thread(
        target=_run, args=(app, task_id, user_id), daemon=True
    )
    thread.start()
    return task_id


def get_task(task_id: str) -> dict | None:
    """Return a deep copy of task state, or None if not found."""
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return None
        return copy.deepcopy(task)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _set(task_id: str, idx: int, status: str, message: str = "") -> None:
    with _lock:
        _tasks[task_id]["steps"][idx]["status"] = status
        _tasks[task_id]["steps"][idx]["message"] = message


def _rows_to_stream(cols: list, rows: list) -> io.BytesIO:
    """Build an in-memory CSV byte stream from cursor results."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    writer.writerows(rows)
    return io.BytesIO(buf.getvalue().encode("utf-8"))


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run(app, task_id: str, user_id: int) -> None:
    # ── Step 0: Connect to ERP ───────────────────────────────────────────────
    _set(task_id, 0, "running")
    try:
        import pyodbc  # noqa: PLC0415 — deferred so a missing driver doesn't break startup

        driver  = os.environ.get("ERP_DB_DRIVER", "InterBase ODBC driver")
        dbname  = os.environ.get("ERP_DB_NAME", "")
        uid     = os.environ.get("ERP_DB_UID", "")
        pwd     = os.environ.get("ERP_DB_PWD", "")
        charset = os.environ.get("ERP_DB_CHARSET", "NONE")

        if not dbname or not uid:
            raise EnvironmentError("ERP_DB_NAME and ERP_DB_UID must be set in .env")

        conn_str = (
            f"Driver={{{driver}}};"
            f"Dbname={dbname};"
            f"UID={uid};"
            f"PWD={pwd};"
            f"CHARSET={charset};"
        )
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        _set(task_id, 0, "success", "Connected")
    except Exception as exc:
        _set(task_id, 0, "failed", str(exc))
        with _lock:
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
        return

    # ── Steps 1…N: Query ERP → stream → import (no disk writes) ─────────────
    with app.app_context():
        from app.admin.routes import _run_importer  # noqa: PLC0415

        for i, ds in enumerate(_DATASETS):
            idx = i + 1
            _set(task_id, idx, "running")
            try:
                qpath = os.path.join(_QUERIES_DIR, ds["query"])
                with open(qpath) as f:
                    query = f.read()

                cursor.execute(query)
                rows = cursor.fetchall()
                cols = [c[0] for c in cursor.description]
                stream = _rows_to_stream(cols, rows)

                batch = _run_importer(ds["import_type"], stream, ds["label"], user_id)

                if batch.status == "success":
                    msg = f"{batch.rows_inserted} inserted, {batch.rows_updated} updated"
                    if batch.rows_closed:
                        msg += f", {batch.rows_closed} closed"
                    _set(task_id, idx, "success", msg)
                else:
                    _set(task_id, idx, "failed", batch.error_message or "Import failed")

            except Exception as exc:
                _set(task_id, idx, "failed", str(exc))
                traceback.print_exc()

        from app.extensions import db  # noqa: PLC0415
        db.session.remove()

    try:
        conn.close()
    except Exception:
        pass

    # ── Finalise ─────────────────────────────────────────────────────────────
    with _lock:
        statuses = {s["status"] for s in _tasks[task_id]["steps"]}
        if "failed" in statuses:
            overall = "partial" if "success" in statuses else "failed"
        else:
            overall = "success"
        _tasks[task_id]["status"] = overall
        _tasks[task_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
