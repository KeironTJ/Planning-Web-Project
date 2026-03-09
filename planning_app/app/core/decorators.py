"""
Reusable route decorators.

Keeping decorators in `core` avoids circular imports between blueprints
and keeps route files clean — each route just declares what it needs.
"""

from functools import wraps
from flask import abort, flash, redirect, url_for, request
from flask_login import current_user


def roles_required(*role_names: str):
    """
    Restrict a route to users who hold **any** of the specified roles.

    Usage::

        @bp.route("/admin-only")
        @login_required
        @roles_required("admin", "planner")
        def admin_view():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.url))
            user_roles = {r.name for r in current_user.roles}
            if not user_roles.intersection(role_names):
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def permission_required(permission_name: str):
    """
    Restrict a route to users who hold a specific permission.

    Usage::

        @bp.route("/plan/create")
        @login_required
        @permission_required("create_work_order")
        def create_work_order():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.url))
            if not current_user.has_permission(permission_name):
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def admin_required(f):
    """Shorthand decorator — equivalent to ``@roles_required("admin")``."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.url))
        if not current_user.is_admin:
            flash("You do not have permission to access that page.", "danger")
            abort(403)
        return f(*args, **kwargs)
    return decorated
