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
from .forms import ImportUploadForm, DeptHoursForm, SystemSettingsForm
from .models import SystemSetting, SETTING_AUTO_COMPLETE_DESPATCH, SETTING_DAILY_OUTPUT_TARGET, SETTING_DAILY_OUTPUT_TARGET_DAYS, SETTING_MRP_LEAD_DAYS
from app.auth.models import User, Role, AuditLog
from app.auth.services import RoleService
from app.extensions import db
from app.core.decorators import admin_required, permission_required
from app.sales.orders.models import Department, ImportBatch


# ---------------------------------------------------------------------------
# Epicor Data Sync
# ---------------------------------------------------------------------------

@admin_bp.route("/epicor-sync")
@login_required
@admin_required
def epicor_sync():
    """Show sync status for every registered Epicor BAQ importer."""
    from app.core.epicor_importers import REGISTRY
    from datetime import date

    last_syncs = {}
    for key, cls in REGISTRY.items():
        batch = (
            ImportBatch.query
            .filter_by(import_type=cls.IMPORT_TYPE)
            .order_by(ImportBatch.uploaded_at.desc())
            .first()
        )
        last_syncs[key] = {"baq_name": cls.BAQ_NAME, "batch": batch}

    today = date.today()
    defaults = {
        "sales_closed_from":      date(today.year, 1, 1).isoformat(),
        "sales_closed_to":        today.isoformat(),
        "production_output_from": (today - __import__("datetime").timedelta(days=7)).isoformat(),
        "production_output_to":   today.isoformat(),
    }

    return render_template(
        "admin/epicor_sync.html",
        title="Epicor Data Sync",
        last_syncs=last_syncs,
        defaults=defaults,
    )


@admin_bp.route("/epicor-sync/run", methods=["POST"])
@login_required
@admin_required
def epicor_sync_run():
    """Trigger one or all BAQ importers (traditional form POST fallback)."""
    from flask import current_app
    from app.core.epicor_client import KineticClient
    from app.core.epicor_importers import REGISTRY, run_batch

    baq_key = request.form.get("baq_key") or None
    if baq_key and baq_key not in REGISTRY:
        flash(f"Unknown BAQ key: {baq_key!r}", "danger")
        return redirect(url_for("admin.epicor_sync"))
    keys = [baq_key] if baq_key else None

    extra_params: dict = {}
    if baq_key == "sales_closed":
        from datetime import date as _date
        def _fmt(iso):
            try: return _date.fromisoformat(iso).strftime("%d/%m/%Y")
            except (ValueError, TypeError): return iso
        extra_params = {
            "OrderDateFrom": _fmt(request.form.get("OrderDateFrom", "")),
            "OrderDateTo":   _fmt(request.form.get("OrderDateTo", "")),
        }
    elif baq_key == "production_output":
        extra_params = {
            "DateFrom": request.form.get("DateFrom", ""),
            "DateTo":   request.form.get("DateTo", ""),
        }

    try:
        with KineticClient.from_app(current_app._get_current_object()) as client:
            if baq_key and extra_params:
                batch = REGISTRY[baq_key](client).run(
                    params=extra_params, triggered_by_id=current_user.id
                )
                results = {baq_key: batch}
            else:
                results = run_batch(client, keys=keys, triggered_by_id=current_user.id)
    except Exception as exc:
        flash(f"Could not connect to Epicor: {exc}", "danger")
        return redirect(url_for("admin.epicor_sync"))

    for key, result in results.items():
        if isinstance(result, Exception):
            flash(f"{key}: {result}", "danger")
        else:
            flash(f"{key}: {result.row_count} fetched / {result.rows_inserted} inserted.", "success")

    return redirect(url_for("admin.epicor_sync"))


@admin_bp.route("/epicor-sync/run-one", methods=["POST"])
@login_required
@admin_required
def epicor_sync_run_one():
    """
    AJAX endpoint: run a single importer and return JSON.

    Expects JSON body: {"baq_key": "stock", "params": {"DateFrom": "2026-01-01"}}
    Returns:          {"status": "ok", "row_count": 123, "rows_inserted": 123}
    """
    from flask import current_app, jsonify
    from app.core.epicor_client import KineticClient
    from app.core.epicor_importers import REGISTRY

    data    = request.get_json(force=True, silent=True) or {}
    baq_key = data.get("baq_key", "")
    params  = data.get("params", {}) or {}

    if not baq_key or baq_key not in REGISTRY:
        return jsonify({"status": "error", "message": f"Unknown importer: {baq_key!r}"}), 400

    # Convert sales_closed date params from ISO to UK format
    if baq_key == "sales_closed":
        from datetime import date as _date
        def _to_uk(iso):
            try: return _date.fromisoformat(iso).strftime("%d/%m/%Y")
            except (ValueError, TypeError): return iso
        if "OrderDateFrom" in params: params["OrderDateFrom"] = _to_uk(params["OrderDateFrom"])
        if "OrderDateTo"   in params: params["OrderDateTo"]   = _to_uk(params["OrderDateTo"])

    try:
        import time
        from sqlalchemy.exc import OperationalError as _OE
        last_exc = None
        for attempt in range(4):           # up to 4 attempts: 0, 2, 4, 8 s backoff
            if attempt:
                time.sleep(2 ** attempt)   # 2, 4, 8 seconds
            try:
                with KineticClient.from_app(current_app._get_current_object()) as client:
                    importer = REGISTRY[baq_key](client)
                    batch = importer.run(
                        params=params if params else None,
                        triggered_by_id=current_user.id,
                    )
                return jsonify({
                    "status":        "ok",
                    "key":           baq_key,
                    "row_count":     batch.row_count,
                    "rows_inserted": batch.rows_inserted,
                    "notes":         batch.notes or "",
                })
            except _OE as db_err:
                last_exc = db_err
                continue   # retry on SQLite lock
        return jsonify({"status": "error", "key": baq_key,
                        "message": f"DB locked after retries: {last_exc}"}), 500
    except Exception as exc:
        return jsonify({"status": "error", "key": baq_key, "message": str(exc)}), 500


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
    """Redirect: departments are now created per-site via Admin → Departments."""
    flash("Departments are now site-scoped. Create them through Admin → Departments.", "info")
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
            dept.flow_order = form.flow_order.data  # None clears it
            dept.track = form.track.data
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
    from app.purchasing.materials.importers import (
        StockImporter, OpenPoImporter, MainMaterialImporter,
    )
    from app.planning.capacity.importers import LabourPlanImporter

    dispatch = {
        "stock":           StockImporter,
        "open_po":         OpenPoImporter,
        "main_material":   MainMaterialImporter,
        "labour_plan":     LabourPlanImporter,
    }
    importer_cls = dispatch[import_type]
    return importer_cls.import_file(stream, uploaded_by_id=user_id, filename=filename)


# ---------------------------------------------------------------------------
# ERP Data Viewers
# ---------------------------------------------------------------------------

@admin_bp.route("/data/main-material")
@login_required
@permission_required("view_materials")
def data_main_material():
    from app.purchasing.materials.models import MaterialRequirementMain
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
        query = query.filter(MaterialRequirementMain.warehouse_code == f_dept)
    rows = query.order_by(MaterialRequirementMain.due_date, MaterialRequirementMain.works_order).paginate(page=page, per_page=50, error_out=False)
    total = MaterialRequirementMain.query.count()
    last = MaterialRequirementMain.query.order_by(MaterialRequirementMain.imported_at.desc()).first()
    from sqlalchemy import distinct
    depts = [r[0] for r in db.session.query(distinct(MaterialRequirementMain.warehouse_code)).filter(MaterialRequirementMain.warehouse_code.isnot(None)).order_by(MaterialRequirementMain.warehouse_code).all()]
    return render_template(
        "admin/data_main_material.html",
        title="Main Material Requirements",
        rows=rows, q=q, f_dept=f_dept, depts=depts, total=total,
        last_imported=last.imported_at if last else None,
    )


# ---------------------------------------------------------------------------
# System Settings
# ---------------------------------------------------------------------------

@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def system_settings():
    form = SystemSettingsForm()

    if form.validate_on_submit():
        SystemSetting.set_bool(
            SETTING_AUTO_COMPLETE_DESPATCH,
            form.auto_complete_despatch.data,
            description=(
                "Automatically mark Despatch as completed when all other "
                "operations for an order line are completed."
            ),
        )
        SystemSetting.set(
            SETTING_DAILY_OUTPUT_TARGET,
            str(form.daily_output_target.data or 0),
            description="Factory daily output target (units).",
        )
        day_map = [
            (0, form.daily_target_mon),
            (1, form.daily_target_tue),
            (2, form.daily_target_wed),
            (3, form.daily_target_thu),
            (4, form.daily_target_fri),
        ]
        target_days_str = ','.join(str(i) for i, f in day_map if f.data)
        SystemSetting.set(
            SETTING_DAILY_OUTPUT_TARGET_DAYS,
            target_days_str or '0,1,2,3',
            description="Weekdays on which the daily target applies (0=Mon, 4=Fri).",
        )
        SystemSetting.set(
            SETTING_MRP_LEAD_DAYS,
            str(form.mrp_lead_days.data if form.mrp_lead_days.data is not None else 14),
            description="Days before ship date that materials must arrive on PO to count as covered.",
        )
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin.system_settings"))

    # Pre-populate form from current DB values
    form.auto_complete_despatch.data = SystemSetting.get_bool(
        SETTING_AUTO_COMPLETE_DESPATCH, default=False
    )
    form.daily_output_target.data = SystemSetting.get_int(
        SETTING_DAILY_OUTPUT_TARGET, default=128
    )
    form.mrp_lead_days.data = SystemSetting.get_int(
        SETTING_MRP_LEAD_DAYS, default=14
    )
    _tdays = set(
        int(d) for d in
        SystemSetting.get(SETTING_DAILY_OUTPUT_TARGET_DAYS, '0,1,2,3').split(',')
        if d.strip().isdigit()
    )
    form.daily_target_mon.data = 0 in _tdays
    form.daily_target_tue.data = 1 in _tdays
    form.daily_target_wed.data = 2 in _tdays
    form.daily_target_thu.data = 3 in _tdays
    form.daily_target_fri.data = 4 in _tdays

    return render_template(
        "admin/settings.html",
        title="System Settings",
        form=form,
    )
