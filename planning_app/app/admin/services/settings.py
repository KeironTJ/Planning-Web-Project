'''Business logic for the Admin portal.'''

"""
Admin blueprint routes.

All routes here require the "admin" role.  The admin_required decorator
from core.decorators enforces this at the HTTP layer.
"""


from flask import render_template, redirect, url_for, flash


from ..forms import SystemSettingsForm
from ..models import SystemSetting, SETTING_AUTO_COMPLETE_DESPATCH, SETTING_DAILY_OUTPUT_TARGET, SETTING_DAILY_OUTPUT_TARGET_DAYS, SETTING_MRP_LEAD_DAYS, SETTING_FABRIC_CLASS_IDS, SETTING_COMPONENT_CLASS_IDS, SETTING_MRP_COMPONENT_LEAD_DAYS
from app.extensions import db


# ---------------------------------------------------------------------------
# Epicor Data Sync
# ---------------------------------------------------------------------------


def handle_system_settings():
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
            description="Days before ship date that fabric/hide materials must arrive on PO to count as covered.",
        )
        SystemSetting.set(
            SETTING_MRP_COMPONENT_LEAD_DAYS,
            str(form.mrp_component_lead_days.data if form.mrp_component_lead_days.data is not None else 14),
            description="Days before ship date that component materials must arrive on PO to count as covered.",
        )
        SystemSetting.set(
            SETTING_FABRIC_CLASS_IDS,
            (form.fabric_class_ids.data or "").strip(),
            description="Epicor class IDs included in fabric/hide availability assessment (comma-separated).",
        )
        SystemSetting.set(
            SETTING_COMPONENT_CLASS_IDS,
            (form.component_class_ids.data or "").strip(),
            description="Epicor class IDs included in component availability assessment (comma-separated; blank = all).",
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
    form.mrp_component_lead_days.data = SystemSetting.get_int(
        SETTING_MRP_COMPONENT_LEAD_DAYS, default=14
    )
    form.fabric_class_ids.data = SystemSetting.get(
        SETTING_FABRIC_CLASS_IDS, "A101,A102,A105,B101,C101,Z102"
    )
    form.component_class_ids.data = SystemSetting.get(
        SETTING_COMPONENT_CLASS_IDS, ""
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
