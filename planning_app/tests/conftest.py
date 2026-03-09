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


@pytest.fixture(scope="session")
def db(app):
    """Session-wide database with all tables created."""
    with app.app_context():
        _db.create_all()
        RoleService.seed_default_roles_and_permissions()
        yield _db
        _db.drop_all()


@pytest.fixture(scope="function", autouse=True)
def db_session(db):
    """
    Wrap each test in a transaction that is rolled back after the test.

    This keeps tests isolated without recreating the schema each time.
    """
    connection = db.engine.connect()
    transaction = connection.begin()
    db.session.bind = connection

    yield db.session

    db.session.remove()
    transaction.rollback()
    connection.close()


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
        "email": email,
        "password": password,
        "remember": False,
    }, follow_redirects=True)
