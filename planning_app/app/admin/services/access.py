'''Business logic for the Admin portal.'''

"""
Admin blueprint routes.

All routes here require the "admin" role.  The admin_required decorator
from core.decorators enforces this at the HTTP layer.
"""


from flask import render_template, redirect, url_for, flash, request


from app.auth.models import User, Role, Permission, AuditLog
from app.auth.services import RoleService
from app.extensions import db
from app.sales.orders.models import Department, ImportBatch


# ---------------------------------------------------------------------------
# Epicor Data Sync
# ---------------------------------------------------------------------------


def handle_dashboard():
    user_count = User.query.count()
    active_count = User.query.filter_by(is_active=True).count()
    role_count = Role.query.count()
    dept_count = Department.query.filter_by(is_active=True).count()
    recent_batches = (
        ImportBatch.query.order_by(ImportBatch.uploaded_at.desc()).limit(5).all()
    )
    recent_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(10).all()
    return render_template(
        "admin/dashboard.html",
        title="Admin Dashboard",
        user_count=user_count,
        active_count=active_count,
        role_count=role_count,
        dept_count=dept_count,
        recent_batches=recent_batches,
        recent_logs=recent_logs,
    )


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

def handle_user_list():
    page = request.args.get("page", 1, type=int)
    users = User.query.order_by(User.username).paginate(page=page, per_page=25, error_out=False)
    return render_template("admin/user_list.html", title="Users", users=users)


def handle_user_detail(user_id: int):
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

def handle_user_create():
    all_roles = Role.query.order_by(Role.name).all()

    if request.method == "POST":
        username   = request.form.get("username", "").strip()
        email      = request.form.get("email", "").strip()
        password   = request.form.get("password", "")
        first_name = request.form.get("first_name", "").strip() or None
        last_name  = request.form.get("last_name", "").strip() or None
        department = request.form.get("department", "").strip() or None
        role_ids   = request.form.getlist("role_ids", type=int)

        errors = []
        if not username:
            errors.append("Username is required.")
        elif User.query.filter_by(username=username).first():
            errors.append(f"Username '{username}' is already taken.")
        if not email:
            errors.append("Email is required.")
        elif User.query.filter_by(email=email).first():
            errors.append(f"Email '{email}' is already registered.")
        if not password:
            errors.append("Password is required.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "admin/user_create.html",
                title="Create User",
                all_roles=all_roles,
                form_data=request.form,
            )

        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            department=department,
            is_active=True,
        )
        user.set_password(password)
        for role in Role.query.filter(Role.id.in_(role_ids)).all():
            user.roles.append(role)
        db.session.add(user)
        db.session.commit()
        flash(f"User '{username}' created successfully.", "success")
        return redirect(url_for("admin.user_detail", user_id=user.id))

    return render_template(
        "admin/user_create.html",
        title="Create User",
        all_roles=all_roles,
        form_data={},
    )


def handle_role_list():
    roles = Role.query.order_by(Role.name).all()
    return render_template("admin/role_list.html", title="Roles & Permissions", roles=roles)


def handle_role_create():
    all_permissions = Permission.query.order_by(Permission.module, Permission.name).all()
    grouped_perms = {}
    for perm in all_permissions:
        grouped_perms.setdefault(perm.module or "other", []).append(perm)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        perm_ids = request.form.getlist("permission_ids", type=int)

        errors = []
        if not name:
            errors.append("Role name is required.")
        elif Role.query.filter_by(name=name).first():
            errors.append(f"Role '{name}' already exists.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "admin/role_create.html",
                title="Create Role",
                all_permissions=all_permissions,
                grouped_perms=grouped_perms,
                form_data=request.form,
            )

        role = Role(name=name, description=description)
        for perm in Permission.query.filter(Permission.id.in_(perm_ids)).all():
            role.permissions.append(perm)
        db.session.add(role)
        db.session.commit()
        flash(f"Role '{name}' created.", "success")
        return redirect(url_for("admin.role_detail", role_id=role.id))

    return render_template(
        "admin/role_create.html",
        title="Create Role",
        all_permissions=all_permissions,
        grouped_perms=grouped_perms,
        form_data=None,
    )


def handle_role_detail(role_id: int):
    role = Role.query.get_or_404(role_id)
    all_permissions = Permission.query.order_by(Permission.module, Permission.name).all()
    grouped_perms = {}
    for perm in all_permissions:
        grouped_perms.setdefault(perm.module or "other", []).append(perm)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_info":
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            if not name:
                flash("Role name is required.", "danger")
            elif name != role.name and Role.query.filter_by(name=name).first():
                flash(f"Role name '{name}' is already taken.", "danger")
            else:
                role.name = name
                role.description = description
                db.session.commit()
                flash("Role updated.", "success")

        elif action == "grant_permission":
            perm_id = request.form.get("permission_id", type=int)
            perm = Permission.query.get(perm_id)
            if perm and perm not in role.permissions:
                role.permissions.append(perm)
                db.session.commit()
                flash(f"Permission '{perm.name}' granted.", "success")

        elif action == "revoke_permission":
            perm_id = request.form.get("permission_id", type=int)
            perm = Permission.query.get(perm_id)
            if perm and perm in role.permissions:
                role.permissions.remove(perm)
                db.session.commit()
                flash(f"Permission '{perm.name}' revoked.", "warning")

        return redirect(url_for("admin.role_detail", role_id=role_id))

    return render_template(
        "admin/role_detail.html",
        title=f"Role: {role.name}",
        role=role,
        grouped_perms=grouped_perms,
    )


def handle_role_delete(role_id: int):
    role = Role.query.get_or_404(role_id)
    if role.name == "admin":
        flash("The admin role cannot be deleted.", "danger")
        return redirect(url_for("admin.role_list"))
    user_count = role.users.count()
    if user_count > 0:
        flash(f"Cannot delete '{role.name}' â€” it is assigned to {user_count} user(s). Revoke it first.", "danger")
        return redirect(url_for("admin.role_detail", role_id=role_id))
    db.session.delete(role)
    db.session.commit()
    flash(f"Role '{role.name}' deleted.", "warning")
    return redirect(url_for("admin.role_list"))


def handle_seed():
    """Seed default roles and permissions (idempotent)."""
    RoleService.seed_default_roles_and_permissions()
    flash("Default roles and permissions have been seeded.", "success")
    return redirect(url_for("admin.role_list"))


def handle_seed_departments():
    """Redirect: departments are now created per-site via Admin â†’ Departments."""
    flash("Departments are now site-scoped. Create them through Admin â†’ Departments.", "info")
    return redirect(url_for("admin.dept_list"))


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

def handle_audit_log():
    from app.auth.models import User
    page       = request.args.get("page", 1, type=int)
    action_f   = request.args.get("action", "").strip()
    user_f     = request.args.get("user", "").strip()
    date_from  = request.args.get("date_from", "").strip()
    date_to    = request.args.get("date_to", "").strip()

    q = AuditLog.query

    if action_f:
        q = q.filter(AuditLog.action.ilike(f"%{action_f}%"))
    if user_f:
        user_ids = [u.id for u in User.query.filter(User.username.ilike(f"%{user_f}%")).all()]
        q = q.filter(AuditLog.user_id.in_(user_ids) if user_ids else db.false())
    if date_from:
        try:
            from datetime import date as _date
            q = q.filter(AuditLog.timestamp >= _date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import date as _date, timedelta
            q = q.filter(AuditLog.timestamp < _date.fromisoformat(date_to) + timedelta(days=1))
        except ValueError:
            pass

    logs = q.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=50, error_out=False)

    # Distinct action values for the dropdown
    actions = [r[0] for r in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]

    return render_template(
        "admin/audit_log.html",
        title="Audit Log",
        logs=logs,
        actions=actions,
        action_f=action_f,
        user_f=user_f,
        date_from=date_from,
        date_to=date_to,
    )


# ---------------------------------------------------------------------------
# Department Management
# ---------------------------------------------------------------------------
