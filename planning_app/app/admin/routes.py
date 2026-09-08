"""Admin blueprint routes."""

from flask_login import login_required

from app.core.decorators import admin_required, permission_required
from . import admin_bp
from . import services


def _admin_handler(handler, *args):
    """Execute an Admin service handler after route-level authorization."""
    return handler(*args)


@admin_bp.route("/epicor-sync")
@login_required
@admin_required
def epicor_sync():
    return _admin_handler(services.handle_epicor_sync)


@admin_bp.route("/epicor-sync/run", methods=["POST"])
@login_required
@admin_required
def epicor_sync_run():
    return _admin_handler(services.handle_epicor_sync_run)


@admin_bp.route("/epicor-sync/run-one", methods=["POST"])
@login_required
@admin_required
def epicor_sync_run_one():
    return _admin_handler(services.handle_epicor_sync_run_one)


@admin_bp.route("/epicor-sync/schedules")
@login_required
@admin_required
def sync_schedules():
    return _admin_handler(services.handle_sync_schedules)


@admin_bp.route("/epicor-sync/schedules/jobs", methods=["POST"])
@login_required
@admin_required
def sync_job_create():
    return _admin_handler(services.handle_sync_job_create)


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>", methods=["POST"])
@login_required
@admin_required
def sync_job_update(job_id: int):
    return _admin_handler(services.handle_sync_job_update, job_id)


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/delete", methods=["POST"])
@login_required
@admin_required
def sync_job_delete(job_id: int):
    return _admin_handler(services.handle_sync_job_delete, job_id)


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/run-now", methods=["POST"])
@login_required
@admin_required
def sync_job_run_now(job_id: int):
    return _admin_handler(services.handle_sync_job_run_now, job_id)


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/status")
@login_required
@admin_required
def sync_job_status(job_id: int):
    return _admin_handler(services.handle_sync_job_status, job_id)


@admin_bp.route("/epicor-sync/schedules/status")
@login_required
@admin_required
def sync_schedules_status():
    return _admin_handler(services.handle_sync_schedules_status)


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/items", methods=["POST"])
@login_required
@admin_required
def sync_job_item_add(job_id: int):
    return _admin_handler(services.handle_sync_job_item_add, job_id)


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/items/<int:item_id>", methods=["POST"])
@login_required
@admin_required
def sync_job_item_update(job_id: int, item_id: int):
    return _admin_handler(services.handle_sync_job_item_update, job_id, item_id)


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/items/<int:item_id>/delete", methods=["POST"])
@login_required
@admin_required
def sync_job_item_delete(job_id: int, item_id: int):
    return _admin_handler(services.handle_sync_job_item_delete, job_id, item_id)


@admin_bp.route("/epicor-sync/schedules/jobs/<int:job_id>/items/<int:item_id>/run-one", methods=["POST"])
@login_required
@admin_required
def sync_job_item_run_one(job_id: int, item_id: int):
    return _admin_handler(services.handle_sync_job_item_run_one, job_id, item_id)


@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    return _admin_handler(services.handle_dashboard)


@admin_bp.route("/users")
@login_required
@admin_required
def user_list():
    return _admin_handler(services.handle_user_list)


@admin_bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@login_required
@admin_required
def user_detail(user_id: int):
    return _admin_handler(services.handle_user_detail, user_id)


@admin_bp.route("/users/create", methods=["GET", "POST"])
@login_required
@admin_required
def user_create():
    return _admin_handler(services.handle_user_create)


@admin_bp.route("/roles")
@login_required
@admin_required
def role_list():
    return _admin_handler(services.handle_role_list)


@admin_bp.route("/roles/create", methods=["GET", "POST"])
@login_required
@admin_required
def role_create():
    return _admin_handler(services.handle_role_create)


@admin_bp.route("/roles/<int:role_id>", methods=["GET", "POST"])
@login_required
@admin_required
def role_detail(role_id: int):
    return _admin_handler(services.handle_role_detail, role_id)


@admin_bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@login_required
@admin_required
def role_delete(role_id: int):
    return _admin_handler(services.handle_role_delete, role_id)


@admin_bp.route("/seed")
@login_required
@admin_required
def seed():
    return _admin_handler(services.handle_seed)


@admin_bp.route("/departments/seed")
@login_required
@admin_required
def seed_departments():
    return _admin_handler(services.handle_seed_departments)


@admin_bp.route("/audit")
@login_required
@admin_required
def audit_log():
    return _admin_handler(services.handle_audit_log)


@admin_bp.route("/departments")
@login_required
@admin_required
def dept_list():
    return _admin_handler(services.handle_dept_list)


@admin_bp.route("/departments/add", methods=["GET", "POST"])
@login_required
@admin_required
def dept_add():
    return _admin_handler(services.handle_dept_add)


@admin_bp.route("/departments/<int:dept_id>", methods=["GET", "POST"])
@login_required
@admin_required
def dept_edit(dept_id: int):
    return _admin_handler(services.handle_dept_edit, dept_id)


@admin_bp.route("/imports")
@login_required
@permission_required("manage_imports")
def import_list():
    return _admin_handler(services.handle_import_list)


@admin_bp.route("/imports/<int:batch_id>")
@login_required
@permission_required("manage_imports")
def import_detail(batch_id: int):
    return _admin_handler(services.handle_import_detail, batch_id)


@admin_bp.route("/imports/upload", methods=["GET", "POST"])
@login_required
@permission_required("manage_imports")
def import_upload():
    return _admin_handler(services.handle_import_upload)


@admin_bp.route("/data/main-material")
@login_required
@permission_required("view_materials")
def data_main_material():
    return _admin_handler(services.handle_data_main_material)


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def system_settings():
    return _admin_handler(services.handle_system_settings)