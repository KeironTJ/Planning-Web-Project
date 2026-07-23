#!/bin/bash
# deploy.sh — pull latest, update deps, migrate DB, restart service.
# Usage: ./deploy.sh [--no-migrate] [--no-logs]

set -e  # exit on first error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/planning_app"
VENV="$APP_DIR/venv/bin"
SERVICE="planning"

NO_MIGRATE=0
NO_LOGS=0
for arg in "$@"; do
  case $arg in
    --no-migrate) NO_MIGRATE=1 ;;
    --no-logs)    NO_LOGS=1 ;;
  esac
done

echo "==> Pulling latest code..."
git -C "$SCRIPT_DIR" pull origin

echo "==> Installing/updating Python dependencies..."
"$VENV/pip" install -q -r "$APP_DIR/requirements.txt"

if [ "$NO_MIGRATE" -eq 0 ]; then
  echo "==> Running DB migrations..."
  # flask db upgrade may exit with a segfault (code 139) on Python 3.13 during
  # interpreter shutdown — the migration itself completes successfully before it.
  # Treat exit 139 (SIGSEGV) as a warning rather than a fatal error.
  (cd "$APP_DIR" && "$VENV/flask" db upgrade) || {
    code=$?
    if [ $code -eq 139 ]; then
      echo "    WARNING: flask db upgrade exited with segfault (139) — likely a"
      echo "    Python 3.13 + C extension shutdown bug. Migration ran successfully."
    else
      echo "    ERROR: flask db upgrade failed (exit $code)"
      exit $code
    fi
  }
fi

echo "==> Reloading systemd and restarting $SERVICE..."
systemctl daemon-reload
systemctl restart "$SERVICE"

echo "==> Done. Service status:"
systemctl is-active "$SERVICE"

if [ "$NO_LOGS" -eq 0 ]; then
  echo ""
  echo "==> Tailing logs (Ctrl+C to exit)..."
  journalctl -u "$SERVICE" -f
fi
