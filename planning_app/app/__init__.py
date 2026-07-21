"""
Application factory.

Centralises app creation so tests can spin up isolated instances and
the WSGI server can import a fully configured app without side effects.
"""

from flask import Flask, render_template
from .config import get_config
from .extensions import db, migrate, login_manager, bcrypt, csrf, jwt, cache, cors
import logging


def _configure_logging(app: Flask) -> None:
    """Send INFO+ from all app loggers to stderr so Gunicorn/journald captures them."""
    if not app.debug and not app.config.get("TESTING"):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        # Suppress noisy third-party loggers
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)


def create_app(config_class=None) -> Flask:
    """
    Create and configure the Flask application.

    Args:
        config_class: Optional config class to override environment detection.
                      Useful in tests: ``create_app(TestingConfig)``.

    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__, instance_relative_config=False)

    # --- Configuration ---
    app.config.from_object(config_class or get_config())

    # --- Logging ---
    _configure_logging(app)

    # --- Initialise Extensions ---
    _init_extensions(app)

    # --- Register Blueprints ---
    _register_blueprints(app)

    # --- Register Error Handlers ---
    _register_error_handlers(app)

    # --- Register Template Helpers ---
    _register_template_globals(app)

    # --- Register CLI Commands ---
    from .core.epicor_commands import epicor_cli
    app.cli.add_command(epicor_cli)

    # --- Start Background Scheduler ---
    # Under Gunicorn the scheduler is started in a single worker via the
    # post_fork hook in gunicorn.conf.py.  For the Flask dev server the
    # scheduler is started here directly (WERKZEUG_RUN_MAIN guard inside
    # init_scheduler prevents duplicate instances under the reloader).
    import os
    if not os.environ.get("GUNICORN_CMD_ARGS") and not _is_gunicorn():
        from .core.scheduler import init_scheduler
        init_scheduler(app)

    return app


def _is_gunicorn() -> bool:
    """Return True when the current process was launched by Gunicorn."""
    import sys
    return any("gunicorn" in arg for arg in sys.argv)


def _init_extensions(app: Flask) -> None:
    """Bind all extensions to the app instance."""
    db.init_app(app)
    _configure_sqlite(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)
    jwt.init_app(app)
    cache.init_app(app)
    # Allow CORS only on API routes
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})


def _configure_sqlite(app: Flask) -> None:
    """Enable WAL mode and a busy timeout for SQLite connections.

    WAL (Write-Ahead Logging) allows concurrent readers while a writer holds
    the DB — preventing "database is locked" errors when the background ERP
    refresh thread writes at the same time as a normal request reads.
    busy_timeout tells SQLite to wait up to 30 s rather than failing immediately.
    No-ops silently for non-SQLite databases.
    """
    import sqlite3
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def _set_wal(dbapi_conn, _record):
        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA busy_timeout=30000")


def _register_blueprints(app: Flask) -> None:
    """Register all feature blueprints."""
    # Import all model modules so Flask-Migrate can detect them
    from .sales.orders import models as _orders_models  # noqa: F401
    from .planning.capacity import models as _capacity_models  # noqa: F401
    from .purchasing.materials import models as _materials_models  # noqa: F401
    from .admin import models as _admin_models  # noqa: F401
    from .operations import models as _operations_models  # noqa: F401
    # SalesOrder model lives in sales.orders.models — already imported above

    from .auth import auth_bp
    from .admin import admin_bp
    from .api.v1 import api_v1_bp

    # --- Department portal blueprints ---
    from .sales import sales_bp
    from .planning import planning_bp
    from .purchasing import purchasing_bp
    from .operations import operations_bp
    from .transport import transport_bp
    from .it import it_bp

    # --- Feature blueprints (nested under their department) ---
    from .sales.orders import orders_bp
    from .planning.capacity import capacity_bp
    from .purchasing.materials import materials_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_v1_bp, url_prefix="/api/v1")

    # Department portals
    app.register_blueprint(sales_bp, url_prefix="/sales")
    app.register_blueprint(planning_bp, url_prefix="/planning")
    app.register_blueprint(purchasing_bp, url_prefix="/purchasing")
    app.register_blueprint(operations_bp, url_prefix="/operations")
    app.register_blueprint(transport_bp, url_prefix="/transport")
    app.register_blueprint(it_bp, url_prefix="/it")

    # Feature modules nested under their department
    app.register_blueprint(orders_bp, url_prefix="/sales/orders")
    app.register_blueprint(capacity_bp, url_prefix="/planning/capacity")
    app.register_blueprint(materials_bp, url_prefix="/purchasing/materials")

    # Root home page
    from flask import redirect, url_for, render_template as _render
    from flask_login import login_required as _login_required, current_user as _current_user

    @app.route("/")
    @_login_required
    def index():
        return _render("home.html", title="Home")


def _register_error_handlers(app: Flask) -> None:
    """Register HTTP error page handlers."""

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()  # Roll back any failed transaction
        return render_template("errors/500.html"), 500


def _register_template_globals(app: Flask) -> None:
    """Inject variables and helpers available in all templates."""

    @app.template_filter("hm")
    def hours_minutes_filter(decimal_hours) -> str:
        """Format a decimal hours value as '2hrs 30mins'."""
        if decimal_hours is None:
            return "—"
        total_mins = int(round(float(decimal_hours) * 60))
        h, m = divmod(total_mins, 60)
        if h == 0:
            return f"{m}min{'s' if m != 1 else ''}"
        return f"{h}hr{'s' if h != 1 else ''} {m:02d}min{'s' if m != 1 else ''}"

    @app.context_processor
    def inject_globals():
        from flask import session
        from flask_login import current_user

        active_departments = []
        active_site = None
        available_sites = []

        if current_user.is_authenticated:
            try:
                from .orders.models import Department
                from .admin.models import Site

                # Resolve the active site from the session (or first available)
                site_id = session.get("active_site_id")
                if site_id:
                    active_site = Site.query.filter_by(id=site_id, is_active=True).first()

                if current_user.is_admin:
                    available_sites = Site.query.filter_by(is_active=True).order_by(Site.name).all()
                else:
                    available_sites = [s for s in current_user.sites if s.is_active]

                # Auto-select a site if none is set and one is available
                if active_site is None and available_sites:
                    active_site = available_sites[0]
                    session["active_site_id"] = active_site.id
                    session["active_site_name"] = active_site.name

                dept_q = Department.query.filter_by(is_active=True)
                if active_site:
                    dept_q = dept_q.filter_by(site_id=active_site.id)
                active_departments = dept_q.order_by(
                    Department.flow_order.asc().nullslast(), Department.name.asc()
                ).all()
            except Exception:
                pass

        return {
            "app_name": app.config.get("APP_NAME", "Planning Hub"),
            "current_year": __import__("datetime").datetime.utcnow().year,
            "active_departments": active_departments,
            "active_site": active_site,
            "available_sites": available_sites,
        }
