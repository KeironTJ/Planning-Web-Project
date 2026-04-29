"""
Authentication and authorisation models.

Design decisions:
- Many-to-many relationship between Users and Roles (a user may have multiple
  roles, e.g. "planner" AND "viewer").
- Many-to-many relationship between Roles and Permissions for fine-grained
  control without code changes.
- Passwords are NEVER stored in plain text — always hashed via bcrypt.
- ``is_admin`` is a convenience property derived from roles, not a DB column,
  so admin access is controlled entirely through the role system.
"""

from datetime import datetime, timezone
from typing import Optional
from app.extensions import db, bcrypt

# Association tables (no model class needed — pure join tables)
user_roles = db.Table(
    "user_roles",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)

user_sites = db.Table(
    "user_sites",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("site_id", db.Integer, db.ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(db.Model):
    """
    A granular capability flag (e.g. "create_work_order", "view_capacity").

    Permissions are assigned to Roles, not directly to Users, keeping the
    permission graph manageable as the user base grows.
    """

    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255))
    module = db.Column(db.String(50), index=True)   # e.g. "capacity", "materials"

    def __repr__(self) -> str:
        return f"<Permission {self.name}>"


class Role(db.Model):
    """
    A named collection of permissions.

    Standard roles:
        admin       — full system access
        planner     — read/write access to planning modules
        viewer      — read-only access
        analyst     — access to forecasting and scenarios
    """

    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255))

    permissions = db.relationship(
        "Permission",
        secondary=role_permissions,
        backref=db.backref("roles", lazy="dynamic"),
        lazy="subquery",
    )

    def has_permission(self, permission_name: str) -> bool:
        return any(p.name == permission_name for p in self.permissions)

    def __repr__(self) -> str:
        return f"<Role {self.name}>"


class User(db.Model):
    """
    Registered application user.

    Flask-Login integration is provided by implementing the four required
    properties/methods: is_authenticated, is_active, is_anonymous, get_id.
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    _password_hash = db.Column("password_hash", db.String(255), nullable=False)
    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    department = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime(timezone=True))
    password_reset_token = db.Column(db.String(255), index=True)
    email_verified = db.Column(db.Boolean, default=False)

    roles = db.relationship(
        "Role",
        secondary=user_roles,
        backref=db.backref("users", lazy="dynamic"),
        lazy="subquery",
    )

    sites = db.relationship(
        "Site",
        secondary=user_sites,
        backref=db.backref("users", lazy="dynamic"),
        lazy="subquery",
    )

    # --- Flask-Login interface ---

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)

    # --- Password management ---

    def set_password(self, plain_password: str) -> None:
        """Hash and store password. Never call with an already-hashed value."""
        self._password_hash = bcrypt.generate_password_hash(plain_password).decode("utf-8")

    def check_password(self, plain_password: str) -> bool:
        """Return True if `plain_password` matches the stored hash."""
        return bcrypt.check_password_hash(self._password_hash, plain_password)

    # --- Role/permission helpers ---

    @property
    def is_admin(self) -> bool:
        return any(r.name == "admin" for r in self.roles)

    def has_role(self, role_name: str) -> bool:
        return any(r.name == role_name for r in self.roles)

    def has_permission(self, permission_name: str) -> bool:
        """Check if any of the user's roles grant this permission."""
        return any(r.has_permission(permission_name) for r in self.roles)

    # --- Display helpers ---

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p) or self.username

    def __repr__(self) -> str:
        return f"<User {self.username}>"


class AuditLog(db.Model):
    """
    Immutable record of security-relevant events.

    Audit logs should NEVER be deleted or updated — they are append-only.
    """

    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False)         # e.g. "login", "password_change"
    resource = db.Column(db.String(100))                        # e.g. "work_order:42"
    ip_address = db.Column(db.String(45))                       # IPv4/IPv6
    user_agent = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    details = db.Column(db.Text)                                # JSON or free text

    user = db.relationship("User", backref=db.backref("audit_logs", lazy="dynamic"))

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} by user_id={self.user_id}>"
