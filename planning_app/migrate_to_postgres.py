#!/usr/bin/env python3
"""
migrate_to_postgres.py — copy config/user data from SQLite → PostgreSQL.

Migrates only manually-managed tables (users, roles, departments, sync jobs,
etc.).  Epicor-sourced tables (sales_orders, works_orders, etc.) are skipped —
they will be repopulated automatically by the first sync after switchover.

Usage (from the planning_app directory):
    python migrate_to_postgres.py

    # Or with explicit connection strings:
    python migrate_to_postgres.py \\
        --sqlite "sqlite:///instance/planning_dev.db" \\
        --postgres "postgresql://planuser:pass@localhost:5432/planning_db"

    # Force overwrite even if destination tables already have rows:
    python migrate_to_postgres.py --force

Reads DATABASE_URL and the sqlite path from .env if not supplied via args.
Run AFTER `flask db upgrade` has already built the schema in PostgreSQL.
"""

from __future__ import annotations

import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Tables to migrate, in foreign-key-safe insertion order.
# Association/junction tables come AFTER both of their parent tables.
# ---------------------------------------------------------------------------
TABLES = [
    # ── no FK dependencies ──────────────────────────────────────────────
    "permissions",
    "roles",
    "sites",
    "system_settings",
    "sync_jobs",
    "sync_schedules",
    # ── depend on tables above ──────────────────────────────────────────
    "users",               # no FK to above, but listed here for clarity
    "departments",         # FK → sites
    "sync_job_items",      # FK → sync_jobs
    # ── capacity buckets: include manually-overridden entries ────────────
    "capacity_buckets",    # FK → departments  (manually_overridden rows)
    # ── association / junction tables ───────────────────────────────────
    "role_permissions",    # FK → roles, permissions
    "user_roles",          # FK → users, roles
    "user_sites",          # FK → users, sites
]

# Tables that have an integer sequence (need resetting after bulk insert)
SEQUENCE_TABLES = {
    "permissions", "roles", "sites", "sync_jobs", "sync_schedules",
    "users", "departments", "sync_job_items", "capacity_buckets",
}

# For capacity_buckets we only want manually-set rows (the rest re-sync from Epicor)
PARTIAL_FILTERS = {
    "capacity_buckets": "manually_overridden = 1",
}


def _load_env() -> None:
    """Load .env from the planning_app directory (one level up from this script's parent)."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for candidate in [
        os.path.join(script_dir, ".env"),
        os.path.join(script_dir, "planning_app", ".env"),
    ]:
        if os.path.exists(candidate):
            try:
                from dotenv import load_dotenv
                load_dotenv(candidate)
                print(f"  Loaded env from {candidate}")
            except ImportError:
                # parse manually if python-dotenv not installed yet
                with open(candidate) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, _, v = line.partition("=")
                            os.environ.setdefault(k.strip(), v.strip())
            return


def _resolve_sqlite_url() -> str:
    """Find the SQLite database file and return a SQLAlchemy URL for it."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        # script lives in planning_app/, so instance/ is a sibling folder
        os.path.join(script_dir, "instance", "planning_dev.db"),
        os.path.join(script_dir, "instance", "planning.db"),
        os.path.join(script_dir, "instance", "planning_production.db"),
    ]
    existing = [p for p in candidates if os.path.exists(p)]
    if existing:
        return f"sqlite:///{existing[0]}"
    raise FileNotFoundError(
        "Could not find the SQLite database file. "
        "Pass --sqlite explicitly, e.g.:\n"
        "  --sqlite sqlite:////absolute/path/to/instance/planning_dev.db"
    )


def migrate(sqlite_url: str, postgres_url: str, force: bool = False) -> None:
    try:
        from sqlalchemy import create_engine, text, MetaData, inspect
    except ImportError:
        print("ERROR: SQLAlchemy not installed. Run: pip install sqlalchemy psycopg2-binary")
        sys.exit(1)

    print(f"\nSource :  {sqlite_url}")
    print(f"Target :  {postgres_url}\n")

    src_engine = create_engine(sqlite_url)
    # Force UTF-8 on the PostgreSQL connection so non-ASCII characters
    # (em dashes, accented letters, etc.) in text columns don't fail.
    dst_engine = create_engine(
        postgres_url,
        connect_args={"client_encoding": "utf8"},
    )

    # Reflect source schema
    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)

    dst_insp = inspect(dst_engine)
    dst_tables = dst_insp.get_table_names()

    errors: list[str] = []

    for table_name in TABLES:
        # ── Source table missing (schema version mismatch?) ──────────────
        if table_name not in src_meta.tables:
            print(f"  SKIP  {table_name:30s} (not in source DB)")
            continue

        # ── Destination table missing (flask db upgrade not run?) ─────────
        if table_name not in dst_tables:
            print(f"  SKIP  {table_name:30s} (not in destination — run flask db upgrade first)")
            errors.append(table_name)
            continue

        src_table = src_meta.tables[table_name]

        # ── Read rows from SQLite ─────────────────────────────────────────
        where_clause = PARTIAL_FILTERS.get(table_name)
        query = str(src_table.select())
        if where_clause:
            query = f"SELECT * FROM {table_name} WHERE {where_clause}"

        with src_engine.connect() as src_conn:
            result = src_conn.execute(text(query) if where_clause else src_table.select())
            rows = result.fetchall()
            columns = result.keys()

        if not rows:
            print(f"  EMPTY {table_name:30s}")
            continue

        row_dicts = [dict(zip(columns, row)) for row in rows]

        # ── Check destination for existing rows ───────────────────────────
        with dst_engine.connect() as dst_conn:
            existing_count = dst_conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()

        if existing_count and not force:
            print(
                f"  SKIP  {table_name:30s} "
                f"({existing_count} rows already in destination — use --force to overwrite)"
            )
            continue

        if existing_count and force:
            with dst_engine.begin() as dst_conn:
                dst_conn.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))
            print(f"  CLEAR {table_name:30s} (truncated {existing_count} existing rows)")

        # ── Insert into PostgreSQL ────────────────────────────────────────
        try:
            dst_meta = MetaData()
            dst_meta.reflect(bind=dst_engine, only=[table_name])
            dst_table = dst_meta.tables[table_name]

            with dst_engine.begin() as dst_conn:
                dst_conn.execute(dst_table.insert(), row_dicts)

            filter_note = f" (where {where_clause})" if where_clause else ""
            print(f"  OK    {table_name:30s} {len(row_dicts)} rows{filter_note}")

            # ── Reset PostgreSQL sequence so future inserts don't collide ─
            if table_name in SEQUENCE_TABLES:
                # Find the PK column name
                pk_cols = [c.name for c in dst_table.primary_key.columns]
                if pk_cols and pk_cols[0] == "id":
                    seq_name = f"{table_name}_id_seq"
                    with dst_engine.begin() as dst_conn:
                        dst_conn.execute(
                            text(f"SELECT setval('{seq_name}', (SELECT MAX(id) FROM {table_name}))")
                        )

        except Exception as exc:
            print(f"  FAIL  {table_name:30s} {exc}")
            errors.append(table_name)

    print()
    if errors:
        print(f"WARNING: {len(errors)} table(s) had errors or were skipped: {', '.join(errors)}")
        print("         Check output above for details.")
    else:
        print("Migration complete. All tables copied successfully.")

    print()
    print("Next: restart the app and trigger a Sync All to repopulate Epicor data.")


def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(description="Migrate config data from SQLite to PostgreSQL.")
    parser.add_argument("--sqlite",   default=None, help="SQLAlchemy URL for the source SQLite DB")
    parser.add_argument("--postgres", default=None, help="SQLAlchemy URL for the destination PostgreSQL DB")
    parser.add_argument("--force",    action="store_true", help="Truncate destination tables and re-insert")
    args = parser.parse_args()

    # Resolve source
    sqlite_url = args.sqlite
    if not sqlite_url:
        try:
            sqlite_url = _resolve_sqlite_url()
        except FileNotFoundError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    # Resolve destination
    postgres_url = args.postgres or os.environ.get("DATABASE_URL", "")
    if not postgres_url or not postgres_url.startswith("postgresql"):
        print(
            "ERROR: PostgreSQL URL not found.\n"
            "  Set DATABASE_URL in .env or pass --postgres postgresql://..."
        )
        sys.exit(1)

    migrate(sqlite_url, postgres_url, force=args.force)


if __name__ == "__main__":
    main()
