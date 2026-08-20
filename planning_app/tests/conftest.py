"""
pytest fixtures for the Planning application test suite.

The app fixture creates a fresh application instance with TestingConfig
for each test session (or module, configurable via scope).  The db fixture
creates all tables before the test and drops them after.
"""

import pytest
from app import create_app
from app.config import TestingConfig
from app.extensions import db as _db
from app.auth.models import User, Role, Permission
from app.auth.services import RoleService


@pytest.fixture(scope="session")
def app():
    """Application instance configured for testing."""
    _app = create_app(TestingConfig)
    _app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )
    yield _app


@pytest.fixture(scope="function")
def db(app):
    """Function-scoped database: fresh schema + seed data for every test.

    SQLite in-memory create_all/drop_all is essentially instant, making
    function scope reliable without the session-bind tricks that were
    removed in SQLAlchemy 2.0.
    """
    with app.app_context():
        _db.create_all()
        RoleService.seed_default_roles_and_permissions()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(scope="function", autouse=True)
def db_session(db):
    """Expose the active session; tests get a clean DB via function-scoped db."""
    yield db.session


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Flask CLI test runner."""
    return app.test_cli_runner()


# ---------------------------------------------------------------------------
# User factories
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_user(db_session):
    """Create and return an admin user."""
    admin_role = Role.query.filter_by(name="admin").first()
    user = User(username="admin_test", email="admin@test.com", is_active=True)
    user.set_password("Admin!Pass1234")
    if admin_role:
        user.roles.append(admin_role)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def planner_user(db_session):
    """Create and return a planner user."""
    planner_role = Role.query.filter_by(name="planner").first()
    user = User(username="planner_test", email="planner@test.com", is_active=True)
    user.set_password("Planner!Pass1234")
    if planner_role:
        user.roles.append(planner_role)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def viewer_user(db_session):
    """Create and return a viewer user."""
    viewer_role = Role.query.filter_by(name="viewer").first()
    user = User(username="viewer_test", email="viewer@test.com", is_active=True)
    user.set_password("Viewer!Pass1234")
    if viewer_role:
        user.roles.append(viewer_role)
    db_session.add(user)
    db_session.commit()
    return user


def login(client, email: str, password: str):
    """Helper: POST to login endpoint and return the response."""
    return client.post("/auth/login", data={
        "login": email,
        "password": password,
        "remember": False,
    }, follow_redirects=True)
