"""
Admin blueprint routes.

All routes here require the "admin" role.  The admin_required decorator
from core.decorators enforces this at the HTTP layer.
"""

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required

from . import admin_bp
from app.auth.models import User, Role, AuditLog
from app.auth.services import RoleService
from app.extensions import db
from app.core.decorators import admin_required
from app.core.exceptions import NotFoundError


@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    user_count = User.query.count()
    active_count = User.query.filter_by(is_active=True).count()
    role_count = Role.query.count()
    recent_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(20).all()
    return render_template(
        "admin/dashboard.html",
        title="Admin Dashboard",
        user_count=user_count,
        active_count=active_count,
        role_count=role_count,
        recent_logs=recent_logs,
    )


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

@admin_bp.route("/users")
@login_required
@admin_required
def user_list():
    page = request.args.get("page", 1, type=int)
    users = User.query.order_by(User.username).paginate(page=page, per_page=25, error_out=False)
    return render_template("admin/user_list.html", title="Users", users=users)


@admin_bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@login_required
@admin_required
def user_detail(user_id: int):
    user = User.query.get_or_404(user_id)
    all_roles = Role.query.order_by(Role.name).all()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "toggle_active":
            user.is_active = not user.is_active
            db.session.commit()
            status = "activated" if user.is_active else "deactivated"
            flash(f"User {user.username} has been {status}.", "success")

        elif action == "assign_role":
            role_id = request.form.get("role_id", type=int)
            role = Role.query.get(role_id)
            if role and role not in user.roles:
                user.roles.append(role)
                db.session.commit()
                flash(f"Role '{role.name}' assigned to {user.username}.", "success")

        elif action == "revoke_role":
            role_id = request.form.get("role_id", type=int)
            role = Role.query.get(role_id)
            if role and role in user.roles:
                user.roles.remove(role)
                db.session.commit()
                flash(f"Role '{role.name}' revoked from {user.username}.", "warning")

        return redirect(url_for("admin.user_detail", user_id=user_id))

    return render_template(
        "admin/user_detail.html",
        title=f"User: {user.username}",
        user=user,
        all_roles=all_roles,
    )


# ---------------------------------------------------------------------------
# Role Management
# ---------------------------------------------------------------------------

@admin_bp.route("/roles")
@login_required
@admin_required
def role_list():
    roles = Role.query.order_by(Role.name).all()
    return render_template("admin/role_list.html", title="Roles & Permissions", roles=roles)


@admin_bp.route("/seed")
@login_required
@admin_required
def seed():
    """Seed default roles and permissions (idempotent)."""
    RoleService.seed_default_roles_and_permissions()
    flash("Default roles and permissions have been seeded.", "success")
    return redirect(url_for("admin.role_list"))


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

@admin_bp.route("/audit")
@login_required
@admin_required
def audit_log():
    page = request.args.get("page", 1, type=int)
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=50, error_out=False)
    return render_template("admin/audit_log.html", title="Audit Log", logs=logs)
