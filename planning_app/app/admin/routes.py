"""
Admin blueprint routes.

All routes here require the "admin" role.  The admin_required decorator
from core.decorators enforces this at the HTTP layer.
"""

import io
from datetime import datetime, timezone

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from . import admin_bp
from .forms import ImportUploadForm, DeptHoursForm
from app.auth.models import User, Role, AuditLog
from app.auth.services import RoleService
from app.extensions import db
from app.core.decorators import admin_required, permission_required
from app.core.exceptions import NotFoundError
from app.orders.models import Department, ImportBatch


@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
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

@admin_bp.route("/users/create", methods=["GET", "POST"])
@login_required
@admin_required
def user_create():
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


@admin_bp.route("/departments/seed")
@login_required
@admin_required
def seed_departments():
    """Seed the default production departments (idempotent)."""
    from app.orders.models import Department

    depts = [
        "WOODMILL",
        "FURNITURE TIMBER",
        "CUTTING",
        "MACHINING",
        "FILLING",
        "UPHOLSTERY",
        "MATTRESS",
        "TACKING",
        "CURTAINS",
        "CURTAIN POLES",
        "BEDDING",
        "BLINDS (CTN SECTION)",
        "DESPATCH",
        "AFTER SALES",
        "BELFIELD TEXTILES",
        "TEK SEATING (CAB SEATS)",
        "DIVAN",
        "ENCAPSULATED SPRINGS",
        "GENERAL",
    ]

    created = 0
    for name in depts:
        code = (
            name.upper()
            .replace(" ", "_")
            .replace("(", "")
            .replace(")", "")
        )
        if not Department.query.filter_by(name=name).first():
            db.session.add(Department(code=code, name=name, is_active=True))
            created += 1
    db.session.commit()

    if created:
        flash(f"{created} department(s) seeded.", "success")
    else:
        flash("All departments already exist — nothing to seed.", "info")
    return redirect(url_for("admin.dept_list"))


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

@admin_bp.route("/audit")
@login_required
@admin_required
def audit_log():
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

@admin_bp.route("/departments")
@login_required
@admin_required
def dept_list():
    departments = Department.query.order_by(
        Department.flow_order.asc().nullslast(), Department.name.asc()
    ).all()
    return render_template("admin/dept_list.html", title="Departments", departments=departments)


@admin_bp.route("/departments/<int:dept_id>", methods=["GET", "POST"])
@login_required
@admin_required
def dept_edit(dept_id: int):
    dept = Department.query.get_or_404(dept_id)
    form = DeptHoursForm(obj=dept)

    if form.validate_on_submit():
        action = request.form.get("action")
        if action == "toggle_active":
            dept.is_active = not dept.is_active
            db.session.commit()
            status = "activated" if dept.is_active else "deactivated"
            flash(f"Department '{dept.name}' {status}.", "success")
        else:
            dept.target_hours_per_day = form.target_hours_per_day.data
            if form.default_lead_time_days.data is not None:
                dept.default_lead_time_days = form.default_lead_time_days.data
            dept.flow_order = form.flow_order.data  # None clears it
            db.session.commit()
            flash(f"Settings updated for {dept.name}.", "success")
        return redirect(url_for("admin.dept_list"))

    return render_template(
        "admin/dept_edit.html",
        title=f"Edit: {dept.name}",
        dept=dept,
        form=form,
    )


# ---------------------------------------------------------------------------
# CSV Import Management
# ---------------------------------------------------------------------------

@admin_bp.route("/imports")
@login_required
@permission_required("manage_imports")
def import_list():
    page = request.args.get("page", 1, type=int)
    import_type = request.args.get("type", "")
    q = ImportBatch.query.order_by(ImportBatch.uploaded_at.desc())
    if import_type:
        q = q.filter_by(import_type=import_type)
    batches = q.paginate(page=page, per_page=30, error_out=False)
    return render_template(
        "admin/import_list.html",
        title="Import History",
        batches=batches,
        import_type=import_type,
    )


@admin_bp.route("/imports/<int:batch_id>")
@login_required
@permission_required("manage_imports")
def import_detail(batch_id: int):
    batch = ImportBatch.query.get_or_404(batch_id)
    return render_template(
        "admin/import_detail.html",
        title=f"Import #{batch.id}",
        batch=batch,
    )


@admin_bp.route("/imports/upload", methods=["GET", "POST"])
@login_required
@permission_required("manage_imports")
def import_upload():
    form = ImportUploadForm()

    if form.validate_on_submit():
        import_type = form.import_type.data
        file_storage = form.file.data
        filename = file_storage.filename
        stream = io.BytesIO(file_storage.read())

        try:
            batch = _run_importer(import_type, stream, filename, current_user.id)
        except Exception as exc:
            flash(f"Import failed: {exc}", "danger")
            return redirect(url_for("admin.import_upload"))

        if batch.status == ImportBatch.STATUS_SUCCESS:
            flash(
                f"Import complete — {batch.rows_inserted} inserted, "
                f"{batch.rows_updated} updated"
                + (f", {batch.rows_closed} closed" if batch.rows_closed else "")
                + ".",
                "success",
            )
        else:
            flash(f"Import failed: {batch.error_message}", "danger")

        return redirect(url_for("admin.import_detail", batch_id=batch.id))

    return render_template("admin/import_upload.html", title="Upload CSV", form=form)


def _run_importer(import_type: str, stream, filename: str, user_id: int) -> ImportBatch:
    """Dispatch to the correct importer class."""
    from app.orders.importers import OobImporter, SmvImporter, ProductionFlowImporter
    from app.materials.importers import (
        StockImporter, OpenPoImporter, MainMaterialImporter, AsMaterialImporter,
    )
    from app.capacity.importers import LabourPlanImporter

    dispatch = {
        "oob":             OobImporter,
        "stock":           StockImporter,
        "open_po":         OpenPoImporter,
        "main_material":   MainMaterialImporter,
        "as_material":     AsMaterialImporter,
        "labour_plan":     LabourPlanImporter,
        "smv":             SmvImporter,
        "production_flow": ProductionFlowImporter,
    }
    importer_cls = dispatch[import_type]
    return importer_cls.import_file(stream, uploaded_by_id=user_id, filename=filename)


# ---------------------------------------------------------------------------
# ERP Data Refresh
# ---------------------------------------------------------------------------

@admin_bp.route("/erp-refresh/start", methods=["POST"])
@login_required
@permission_required("manage_imports")
def erp_refresh_start():
    """Start a background ERP export + import cycle. Returns {task_id}."""
    from flask import current_app
    from app.admin.erp_refresh import start_refresh

    task_id = start_refresh(current_app._get_current_object(), current_user.id)
    return jsonify({"task_id": task_id})


@admin_bp.route("/erp-refresh/status/<task_id>")
@login_required
@permission_required("manage_imports")
def erp_refresh_status(task_id: str):
    """Poll the status of a running or completed ERP refresh task."""
    from app.admin.erp_refresh import get_task

    task = get_task(task_id)
    if task is None:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


# ---------------------------------------------------------------------------
# ERP Data Viewers
# ---------------------------------------------------------------------------

@admin_bp.route("/data/main-material")
@login_required
@permission_required("view_materials")
def data_main_material():
    from app.materials.models import MaterialRequirementMain
    q = request.args.get("q", "").strip()
    f_dept = request.args.get("dept", "").strip()
    page = request.args.get("page", 1, type=int)
    query = MaterialRequirementMain.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            MaterialRequirementMain.works_order.ilike(like),
            MaterialRequirementMain.material_code.ilike(like),
            MaterialRequirementMain.material_description.ilike(like),
        ))
    if f_dept:
        query = query.filter(MaterialRequirementMain.department == f_dept)
    rows = query.order_by(MaterialRequirementMain.due_date, MaterialRequirementMain.works_order).paginate(page=page, per_page=50, error_out=False)
    total = MaterialRequirementMain.query.count()
    last = MaterialRequirementMain.query.order_by(MaterialRequirementMain.imported_at.desc()).first()
    from sqlalchemy import distinct
    depts = [r[0] for r in db.session.query(distinct(MaterialRequirementMain.department)).filter(MaterialRequirementMain.department.isnot(None)).order_by(MaterialRequirementMain.department).all()]
    return render_template(
        "admin/data_main_material.html",
        title="Main Material Requirements",
        rows=rows, q=q, f_dept=f_dept, depts=depts, total=total,
        last_imported=last.imported_at if last else None,
    )


@admin_bp.route("/data/after-sales")
@login_required
@permission_required("view_materials")
def data_after_sales():
    from app.materials.models import MaterialRequirementAfterSales
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    query = MaterialRequirementAfterSales.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            MaterialRequirementAfterSales.order_number.ilike(like),
            MaterialRequirementAfterSales.product_code.ilike(like),
            MaterialRequirementAfterSales.customer.ilike(like),
        ))
    rows = query.order_by(MaterialRequirementAfterSales.due_date, MaterialRequirementAfterSales.order_number).paginate(page=page, per_page=50, error_out=False)
    total = MaterialRequirementAfterSales.query.count()
    last = MaterialRequirementAfterSales.query.order_by(MaterialRequirementAfterSales.imported_at.desc()).first()
    return render_template(
        "admin/data_after_sales.html",
        title="AfterSales Material Requirements",
        rows=rows, q=q, total=total,
        last_imported=last.imported_at if last else None,
    )

