"""Admin service facade grouped by domain."""

from .access import (
    handle_audit_log,
    handle_dashboard,
    handle_role_create,
    handle_role_delete,
    handle_role_detail,
    handle_role_list,
    handle_seed,
    handle_seed_departments,
    handle_user_create,
    handle_user_detail,
    handle_user_list,
)
from .departments import handle_dept_add, handle_dept_edit, handle_dept_list
from .imports import (
    handle_data_main_material,
    handle_import_detail,
    handle_import_list,
    handle_import_upload,
)
from .settings import handle_system_settings
from .sync import (
    handle_epicor_sync,
    handle_epicor_sync_run,
    handle_epicor_sync_run_one,
    handle_sync_job_create,
    handle_sync_job_delete,
    handle_sync_job_item_add,
    handle_sync_job_item_delete,
    handle_sync_job_item_run_one,
    handle_sync_job_item_update,
    handle_sync_job_run_now,
    handle_sync_job_status,
    handle_sync_job_update,
    handle_sync_schedules,
    handle_sync_schedules_status,
)