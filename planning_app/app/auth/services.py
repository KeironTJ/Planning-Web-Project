"""
Auth service layer.

Business logic lives here — routes call services, services call repositories.
This makes the logic testable without spinning up a HTTP layer.
"""

from datetime import datetime, timezone
from typing import Optional
from flask import current_app, request

from app.extensions import db
from app.core.security import (
    generate_token, verify_token,
    check_password_strength, record_failed_login,
    is_locked_out, clear_login_attempts,
)
from app.core.exceptions import (
    NotFoundError, ValidationError, AuthorisationError, DuplicateError
)
from .models import User, Role, Permission, AuditLog


class AuthService:
    """Handles registration, login, logout, and password management."""

    @staticmethod
    def register_user(
        username: str,
        email: str,
        password: str,
        first_name: str = "",
        last_name: str = "",
        department: str = "",
        default_role: str = "viewer",
    ) -> User:
        """
        Create a new user account.

        The default role is "viewer" so new accounts have read-only access
        until an admin promotes them.
        """
        email = email.lower().strip()
        if User.query.filter_by(email=email).first():
            raise DuplicateError(f"An account with email '{email}' already exists.")
        if User.query.filter_by(username=username).first():
            raise DuplicateError(f"Username '{username}' is already taken.")

        is_valid, errors = check_password_strength(password)
        if not is_valid:
            raise ValidationError("; ".join(errors))

        role = Role.query.filter_by(name=default_role).first()
        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            department=department,
            is_active=True,
        )
        user.set_password(password)
        if role:
            user.roles.append(role)
        db.session.add(user)
        db.session.commit()

        AuthService._log(user.id, "register", "user")
        return user

    @staticmethod
    def login(login: str, password: str, ip: str = "") -> User:
        """
        Authenticate a user by username or email and password.

        Implements account lockout after repeated failures to mitigate
        brute-force attacks.
        """
        identifier = ip or login
        if is_locked_out(identifier):
            raise AuthorisationError(
                "Too many failed login attempts. Please try again in 15 minutes."
            )

        login = login.strip()
        user = (
            User.query.filter_by(email=login.lower()).first()
            or User.query.filter_by(username=login).first()
        )
        if not user or not user.check_password(password):
            record_failed_login(identifier)
            raise AuthorisationError("Invalid username/email or password.")

        if not user.is_active:
            raise AuthorisationError("Your account has been deactivated.")

        clear_login_attempts(identifier)
        user.last_login = datetime.now(timezone.utc)
        db.session.commit()
        AuthService._log(user.id, "login", "user")
        return user

    @staticmethod
    def generate_password_reset_token(email: str) -> Optional[str]:
        """
        Return a signed reset token for `email` if the account exists.

        We deliberately return None (not raise) if the email is not found
        to avoid leaking account existence via timing differences.
        """
        user = User.query.filter_by(email=email.lower().strip()).first()
        if not user:
            return None
        token = generate_token(email, salt="password-reset")
        user.password_reset_token = token
        db.session.commit()
        return token

    @staticmethod
    def reset_password(token: str, new_password: str) -> User:
        """Apply a new password using a valid reset token."""
        email = verify_token(token, salt="password-reset", max_age=3600)
        if not email:
            raise ValidationError("The password reset link is invalid or has expired.")

        user = User.query.filter_by(email=email).first()
        if not user:
            raise NotFoundError("Account not found.")

        is_valid, errors = check_password_strength(new_password)
        if not is_valid:
            raise ValidationError("; ".join(errors))

        user.set_password(new_password)
        user.password_reset_token = None
        db.session.commit()
        AuthService._log(user.id, "password_reset", "user")
        return user

    @staticmethod
    def change_password(user: User, current_password: str, new_password: str) -> None:
        """Allow an authenticated user to change their own password."""
        if not user.check_password(current_password):
            raise AuthorisationError("Current password is incorrect.")

        is_valid, errors = check_password_strength(new_password)
        if not is_valid:
            raise ValidationError("; ".join(errors))

        user.set_password(new_password)
        db.session.commit()
        AuthService._log(user.id, "password_change", "user")

    @staticmethod
    def _log(user_id: Optional[int], action: str, resource: str, details: str = "") -> None:
        """Write an audit log entry."""
        try:
            entry = AuditLog(
                user_id=user_id,
                action=action,
                resource=resource,
                ip_address=request.remote_addr if request else None,
                user_agent=request.user_agent.string if request else None,
                details=details,
            )
            db.session.add(entry)
            db.session.commit()
        except Exception:
            # Never let audit logging crash the main flow
            db.session.rollback()


class RoleService:
    """Manages roles, permissions, and user-role assignments."""

    @staticmethod
    def seed_default_roles_and_permissions() -> None:
        """
        Create the default roles and permissions if they don't already exist.

        Called by the ``seed-db`` CLI command after the first migration.
        """
        permissions_data = [
            # Orders / WIP
            ("view_orders", "View WIP tracker and order lists", "orders"),
            ("update_order_status", "Update works order operation status", "orders"),
            ("manage_orders", "Edit planned dates and notes on operations", "orders"),
            # Capacity
            ("view_capacity", "View capacity dashboard and labour plan", "capacity"),
            ("override_capacity", "Override capacity bucket available hours", "capacity"),
            # Materials
            ("view_materials", "View stock, purchase orders, and shortage report", "materials"),
            # Admin
            ("manage_imports", "Upload and manage CSV data imports", "admin"),
            ("manage_departments", "Configure department settings", "admin"),
            ("manage_users", "Create, edit, and deactivate users", "admin"),
            ("manage_roles", "Assign roles and permissions", "admin"),
        ]

        perm_objects: dict[str, Permission] = {}
        for name, desc, module in permissions_data:
            perm = Permission.query.filter_by(name=name).first()
            if not perm:
                perm = Permission(name=name, description=desc, module=module)
                db.session.add(perm)
            perm_objects[name] = perm
        db.session.flush()

        roles_data = {
            "admin": {
                "description": "Full system access",
                "permissions": list(perm_objects.keys()),
            },
            "planner": {
                "description": "Read/write access to all planning modules",
                "permissions": [
                    "view_orders", "update_order_status", "manage_orders",
                    "view_capacity", "override_capacity",
                    "view_materials",
                    "manage_imports",
                ],
            },
            "viewer": {
                "description": "Read-only access",
                "permissions": [
                    "view_orders", "view_capacity", "view_materials",
                ],
            },
            "production_operative": {
                "description": "Shop-floor operative — view orders and update operation status",
                "permissions": [
                    "view_orders", "update_order_status",
                ],
            },
            "production_supervisor": {
                "description": "Section supervisor — plan dates, view capacity and materials",
                "permissions": [
                    "view_orders", "update_order_status", "manage_orders",
                    "view_capacity", "view_materials",
                ],
            },
            "production_manager": {
                "description": "Production manager — full production visibility including capacity override",
                "permissions": [
                    "view_orders", "update_order_status", "manage_orders",
                    "view_capacity", "override_capacity",
                    "view_materials",
                ],
            },
        }

        for role_name, role_data in roles_data.items():
            role = Role.query.filter_by(name=role_name).first()
            if not role:
                role = Role(name=role_name, description=role_data["description"])
                db.session.add(role)
            for perm_name in role_data["permissions"]:
                perm = perm_objects.get(perm_name)
                if perm and perm not in role.permissions:
                    role.permissions.append(perm)

        db.session.commit()

    @staticmethod
    def assign_role(user: User, role_name: str) -> None:
        role = Role.query.filter_by(name=role_name).first()
        if not role:
            raise NotFoundError(f"Role '{role_name}' does not exist.")
        if role not in user.roles:
            user.roles.append(role)
            db.session.commit()

    @staticmethod
    def revoke_role(user: User, role_name: str) -> None:
        role = Role.query.filter_by(name=role_name).first()
        if role and role in user.roles:
            user.roles.remove(role)
            db.session.commit()
