"""
Flask extension singletons.

Extensions are initialised here without an app, then bound to the app
inside the factory via `init_app()`.  This avoids circular imports and
supports the application factory pattern.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
from flask_jwt_extended import JWTManager
from flask_caching import Cache
from flask_cors import CORS

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
bcrypt = Bcrypt()
csrf = CSRFProtect()
jwt = JWTManager()
cache = Cache()
cors = CORS()

# Configure login manager defaults
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"
