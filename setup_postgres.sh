#!/bin/bash
# setup_postgres.sh — one-time PostgreSQL install and database creation.
# Run this ONCE on a fresh server before the first deploy.
# Safe to re-run (all steps are idempotent).
#
# Usage:
#   PLANNING_DB_PASS=mysecretpassword ./setup_postgres.sh
#
# Optional overrides (defaults shown):
#   PLANNING_DB_NAME=planning_db
#   PLANNING_DB_USER=planuser

set -e

DB_NAME="${PLANNING_DB_NAME:-planning_db}"
DB_USER="${PLANNING_DB_USER:-planuser}"
DB_PASS="${PLANNING_DB_PASS:-}"

if [ -z "$DB_PASS" ]; then
  echo "ERROR: PLANNING_DB_PASS is required."
  echo ""
  echo "  Usage: PLANNING_DB_PASS=mysecretpassword ./setup_postgres.sh"
  exit 1
fi

# --- 1. Install PostgreSQL if not already installed --------------------
if command -v psql &>/dev/null; then
  echo "==> PostgreSQL already installed ($(psql --version))"
else
  echo "==> Installing PostgreSQL..."
  sudo apt-get update -q
  sudo apt-get install -y postgresql postgresql-contrib
  sudo systemctl enable postgresql
  sudo systemctl start postgresql
  echo "==> PostgreSQL installed and started."
fi

# --- 2. Create DB user -------------------------------------------------
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  echo "==> DB user '$DB_USER' already exists — skipping."
else
  echo "==> Creating DB user '$DB_USER'..."
  sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
fi

# --- 3. Create database -----------------------------------------------
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  echo "==> Database '$DB_NAME' already exists — skipping."
else
  echo "==> Creating database '$DB_NAME'..."
  sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
  # Grant all privileges (needed for migrations to create/alter tables)
  sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
fi

# --- 4. Done ----------------------------------------------------------
echo ""
echo "==> PostgreSQL setup complete."
echo ""
echo "Add this line to your production .env file:"
echo ""
echo "    DATABASE_URL=postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"
echo ""
echo "Next steps:"
echo "  1. Update .env with the DATABASE_URL above"
echo "  2. Run: ./deploy.sh            (installs psycopg2, runs flask db upgrade)"
echo "  3. Run: python migrate_to_postgres.py  (copies users, roles, departments, etc.)"
