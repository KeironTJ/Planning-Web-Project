"""
Application configuration.

Supports three environments via FLASK_ENV:
    - development (default)
    - testing
    - production

Design decision: Base config holds defaults; child classes override only what
differs per environment.  The factory function `get_config` selects the right
class so the app factory never has to inspect env vars directly.
"""

import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class BaseConfig:
    """Shared defaults for all environments."""

    # --- Core ---
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    APP_NAME: str = os.environ.get("APP_NAME", "Planning Hub")
    COMPANY_NAME: str = os.environ.get("COMPANY_NAME", "Belfield Furnishings Ltd")

    # --- Database ---
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ENGINE_OPTIONS: dict = {
        "pool_pre_ping": True,      # Recover from dropped connections
        "pool_recycle": 3600,       # Recycle connections after 1 hour
    }

    # --- Session ---
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(hours=8)
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"

    # --- JWT ---
    JWT_SECRET_KEY: str = os.environ.get("JWT_SECRET_KEY", "jwt-secret-change-in-production")
    JWT_ACCESS_TOKEN_EXPIRES: timedelta = timedelta(hours=1)
    JWT_REFRESH_TOKEN_EXPIRES: timedelta = timedelta(days=30)

    # --- WTF CSRF ---
    WTF_CSRF_ENABLED: bool = True
    WTF_CSRF_TIME_LIMIT: int = 3600

    # --- Caching ---
    CACHE_TYPE: str = "SimpleCache"
    CACHE_DEFAULT_TIMEOUT: int = 300

    # --- Pagination ---
    ITEMS_PER_PAGE: int = 25

    # --- Mail ---
    MAIL_SERVER: str = os.environ.get("MAIL_SERVER", "localhost")
    MAIL_PORT: int = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS: bool = bool(int(os.environ.get("MAIL_USE_TLS", 1)))
    MAIL_USERNAME: str = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD: str = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER: str = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@localhost")


class DevelopmentConfig(BaseConfig):
    """Local development settings.

    Defaults to SQLite so no database server is required to get started.
    Set DATABASE_URL in .env to switch to PostgreSQL.
    """

    DEBUG: bool = True
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL", "sqlite:///planning_dev.db"
    )
    # Echo SQL to console in dev — useful for debugging N+1 queries
    SQLALCHEMY_ECHO: bool = False
    SESSION_COOKIE_SECURE: bool = False


class TestingConfig(BaseConfig):
    """Automated test settings.

    Uses an in-memory SQLite database — no server required.
    CSRF is disabled to allow form submission in tests.
    """

    TESTING: bool = True
    WTF_CSRF_ENABLED: bool = False
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "TEST_DATABASE_URL",
        "sqlite:///:memory:",
    )
    SQLALCHEMY_ECHO: bool = False
    SESSION_COOKIE_SECURE: bool = False
    # Faster password hashing in tests
    BCRYPT_LOG_ROUNDS: int = 4


class ProductionConfig(BaseConfig):
    """Production hardening."""

    DEBUG: bool = False
    TESTING: bool = False
    SQLALCHEMY_DATABASE_URI: str = os.environ.get("DATABASE_URL", "")
    SESSION_COOKIE_SECURE: bool = True   # HTTPS only
    REMEMBER_COOKIE_SECURE: bool = True
    CACHE_TYPE: str = os.environ.get("CACHE_TYPE", "RedisCache")
    CACHE_REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    @classmethod
    def validate(cls) -> None:
        """Raise if critical production settings are missing."""
        required = ["SECRET_KEY", "JWT_SECRET_KEY", "DATABASE_URL"]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}"
            )


# Map FLASK_ENV values to config classes
_CONFIG_MAP: dict = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config() -> type:
    """Return the config class for the current environment."""
    env = os.environ.get("FLASK_ENV", "development").lower()
    config_class = _CONFIG_MAP.get(env, DevelopmentConfig)
    if env == "production":
        config_class.validate()
    return config_class
