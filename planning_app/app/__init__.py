"""
Application factory.

Centralises app creation so tests can spin up isolated instances and
the WSGI server can import a fully configured app without side effects.
"""

from flask import Flask, render_template
from .config import get_config
from .extensions import db, migrate, login_manager, bcrypt, csrf, jwt, cache, cors


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

    # --- Initialise Extensions ---
    _init_extensions(app)

    # --- Register Blueprints ---
    _register_blueprints(app)

    # --- Register Error Handlers ---
    _register_error_handlers(app)

    # --- Register Template Helpers ---
    _register_template_globals(app)

    return app


def _init_extensions(app: Flask) -> None:
    """Bind all extensions to the app instance."""
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)
    jwt.init_app(app)
    cache.init_app(app)
    # Allow CORS only on API routes
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})


def _register_blueprints(app: Flask) -> None:
    """Register all feature blueprints."""
    from .auth import auth_bp
    from .admin import admin_bp
    from .capacity import capacity_bp
    from .materials import materials_bp
    from .forecasting import forecasting_bp
    from .workload import workload_bp
    from .scenarios import scenarios_bp
    from .api.v1 import api_v1_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(capacity_bp, url_prefix="/capacity")
    app.register_blueprint(materials_bp, url_prefix="/materials")
    app.register_blueprint(forecasting_bp, url_prefix="/forecasting")
    app.register_blueprint(workload_bp, url_prefix="/workload")
    app.register_blueprint(scenarios_bp, url_prefix="/scenarios")
    app.register_blueprint(api_v1_bp, url_prefix="/api/v1")

    # Root redirect
    from flask import redirect, url_for

    @app.route("/")
    def index():
        return redirect(url_for("capacity.dashboard"))


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
    import datetime

    @app.context_processor
    def inject_globals():
        return {
            "app_name": app.config.get("APP_NAME", "Planning Hub"),
            "company_name": app.config.get("COMPANY_NAME", ""),
            "current_year": datetime.datetime.utcnow().year,
        }
