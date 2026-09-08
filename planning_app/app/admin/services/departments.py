'''Business logic for the Admin portal.'''

"""
Admin blueprint routes.

All routes here require the "admin" role.  The admin_required decorator
from core.decorators enforces this at the HTTP layer.
"""


from flask import render_template, redirect, url_for, flash, request


from ..forms import DeptHoursForm, DeptCreateForm
from app.extensions import db
from app.sales.orders.models import Department


# ---------------------------------------------------------------------------
# Epicor Data Sync
# ---------------------------------------------------------------------------


def handle_dept_list():
    departments = Department.query.order_by(
        Department.flow_order.asc().nullslast(), Department.name.asc()
    ).all()
    return render_template("admin/dept_list.html", title="Departments", departments=departments)


def handle_dept_add():
    form = DeptCreateForm()
    if request.method == "GET":
        form.track.data = True
    if form.validate_on_submit():
        code = form.code.data.strip().upper()
        if Department.query.filter_by(code=code).first():
            flash(f"A department with code '{code}' already exists.", "danger")
        else:
            dept = Department(
                code=code,
                name=form.name.data.strip(),
                target_hours_per_day=form.target_hours_per_day.data,
                flow_order=form.flow_order.data,
                op_code=form.op_code.data.strip().upper() or None,
                track=form.track.data,
                is_active=True,
            )
            db.session.add(dept)
            db.session.commit()
            flash(f"Department '{dept.name}' created.", "success")
            return redirect(url_for("admin.dept_list"))
    return render_template("admin/dept_add.html", title="Add Department", form=form)


def handle_dept_edit(dept_id: int):
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
            dept.op_code = form.op_code.data.strip().upper() or None
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
