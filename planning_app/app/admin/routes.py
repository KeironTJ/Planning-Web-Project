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
from app.core.decorators import admin_required
from app.core.exceptions import NotFoundError
from app.orders.models import Department, ImportBatch, SmvMatrix


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
    page = request.args.get("page", 1, type=int)
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=50, error_out=False)
    return render_template("admin/audit_log.html", title="Audit Log", logs=logs)


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
@admin_required
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
@admin_required
def import_detail(batch_id: int):
    batch = ImportBatch.query.get_or_404(batch_id)
    return render_template(
        "admin/import_detail.html",
        title=f"Import #{batch.id}",
        batch=batch,
    )


@admin_bp.route("/imports/upload", methods=["GET", "POST"])
@login_required
@admin_required
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
# SMV Management
# ---------------------------------------------------------------------------

@admin_bp.route("/smv")
@login_required
@admin_required
def smv_list():
    from math import ceil

    page       = request.args.get("page", 1, type=int)
    search     = request.args.get("q", "").strip()
    f_timing   = request.args.get("timing_code", "").strip()
    f_conf     = request.args.get("confidence", "").strip()
    f_dept_id  = request.args.get("dept_id", 0, type=int)

    # Column headers — active departments ordered by flow
    departments = (
        Department.query
        .filter_by(is_active=True)
        .order_by(Department.flow_order.asc().nullslast(), Department.name.asc())
        .all()
    )

    # Distinct timing codes for the filter dropdown
    timing_codes = [
        r[0] for r in
        db.session.query(SmvMatrix.timing_code)
        .filter(SmvMatrix.timing_code.isnot(None))
        .distinct()
        .order_by(SmvMatrix.timing_code)
        .all()
    ]

    # --- Build the set of matching component_ids via filters ---
    cid_q = db.session.query(SmvMatrix.component_id).distinct()

    if search:
        like = f"%{search}%"
        cid_q = cid_q.filter(db.or_(
            SmvMatrix.component_id.ilike(like),
            SmvMatrix.description.ilike(like),
            SmvMatrix.timing_code.ilike(like),
        ))
    if f_timing:
        cid_q = cid_q.filter(SmvMatrix.timing_code == f_timing)
    if f_conf:
        cid_q = cid_q.filter(SmvMatrix.confidence == f_conf)
    if f_dept_id:
        cid_q = cid_q.filter(
            SmvMatrix.department_id == f_dept_id,
            SmvMatrix.smv_minutes > 0,
        )

    matching_cids = sorted([r[0] for r in cid_q.all()])

    # --- Fetch ALL entries for matching components (full matrix rows) ---
    all_entries = (
        SmvMatrix.query
        .filter(SmvMatrix.component_id.in_(matching_cids))
        .order_by(SmvMatrix.component_id.asc())
        .all()
    ) if matching_cids else []

    # Build pivot
    pivot = {}
    for entry in all_entries:
        cid = entry.component_id
        if cid not in pivot:
            pivot[cid] = {
                "component_id": cid,
                "timing_code": entry.timing_code,
                "description": entry.description,
                "cells": {},
            }
        pivot[cid]["cells"][entry.department_id] = entry

    _conf_rank = {
        SmvMatrix.CONFIDENCE_ESTIMATED: 0,
        SmvMatrix.CONFIDENCE_TIMED:     1,
        SmvMatrix.CONFIDENCE_MOST:      2,
    }
    for row in pivot.values():
        row["ops_count"] = sum(
            1 for e in row["cells"].values()
            if e.smv_minutes is not None and e.smv_minutes > 0
        )
        # Row confidence = lowest (least certain) across all cells with smv > 0
        active_cells = [e for e in row["cells"].values() if e.smv_minutes and e.smv_minutes > 0]
        if active_cells:
            row["confidence"] = min(active_cells, key=lambda e: _conf_rank.get(e.confidence, 0)).confidence
        else:
            row["confidence"] = None

        # Most recent manual edit across all cells
        edited_cells = [e for e in row["cells"].values() if e.last_modified_at]
        if edited_cells:
            latest = max(edited_cells, key=lambda e: e.last_modified_at)
            row["last_modified_at"] = latest.last_modified_at
            row["last_modified_by"] = latest.last_modified_by
        else:
            row["last_modified_at"] = None
            row["last_modified_by"] = None

    # Paginate on sorted component_ids
    total      = len(matching_cids)
    per_page   = 50
    start      = (page - 1) * per_page
    total_pages = ceil(total / per_page) if total else 1
    rows        = [pivot[cid] for cid in matching_cids[start : start + per_page] if cid in pivot]

    return render_template(
        "admin/smv_list.html",
        title="SMV Matrix",
        rows=rows,
        departments=departments,
        timing_codes=timing_codes,
        search=search,
        f_timing=f_timing,
        f_conf=f_conf,
        f_dept_id=f_dept_id,
        page=page,
        total=total,
        total_pages=total_pages,
        per_page=per_page,
        has_prev=page > 1,
        has_next=page < total_pages,
        confidence_choices=[
            (SmvMatrix.CONFIDENCE_ESTIMATED, "Estimated"),
            (SmvMatrix.CONFIDENCE_TIMED,     "Timed Study"),
            (SmvMatrix.CONFIDENCE_MOST,      "MOST Study"),
        ],
    )


@admin_bp.route("/smv/<int:smv_id>/edit", methods=["POST"])
@login_required
@admin_required
def smv_edit(smv_id: int):
    from decimal import Decimal, InvalidOperation

    entry = SmvMatrix.query.get_or_404(smv_id)

    smv_minutes = request.form.get("smv_minutes", "").strip()
    confidence  = request.form.get("confidence", "").strip()

    if smv_minutes:
        try:
            entry.smv_minutes = Decimal(smv_minutes)
        except InvalidOperation:
            flash("Invalid SMV value — must be a number.", "danger")
            return redirect(request.referrer or url_for("admin.smv_list"))
    else:
        entry.smv_minutes = None

    valid_confidences = {
        SmvMatrix.CONFIDENCE_ESTIMATED,
        SmvMatrix.CONFIDENCE_TIMED,
        SmvMatrix.CONFIDENCE_MOST,
    }
    if confidence in valid_confidences:
        entry.confidence = confidence

    entry.last_modified_at = datetime.now(timezone.utc)
    entry.last_modified_by_id = current_user.id

    db.session.commit()
    flash(f"SMV updated for {entry.component_id} / {entry.department.name}.", "success")
    return redirect(request.referrer or url_for("admin.smv_list"))
