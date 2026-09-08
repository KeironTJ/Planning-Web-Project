'''Business logic for the Admin portal.'''

"""
Admin blueprint routes.

All routes here require the "admin" role.  The admin_required decorator
from core.decorators enforces this at the HTTP layer.
"""


from flask import render_template, redirect, url_for, flash, request
from flask_login import current_user


from ..forms import ImportUploadForm
from app.extensions import db
from app.sales.orders.models import ImportBatch


# ---------------------------------------------------------------------------
# Epicor Data Sync
# ---------------------------------------------------------------------------


def handle_import_list():
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


def handle_import_detail(batch_id: int):
    batch = ImportBatch.query.get_or_404(batch_id)
    return render_template(
        "admin/import_detail.html",
        title=f"Import #{batch.id}",
        batch=batch,
    )


def handle_import_upload():
    form = ImportUploadForm()

    if form.validate_on_submit():
        import_type = form.import_type.data
        file_storage = form.file.data
        filename = file_storage.filename

        try:
            batch = run_importer(
                import_type, file_storage.stream, filename, current_user.id
            )
        except Exception as exc:
            flash(f"Import failed: {exc}", "danger")
            return redirect(url_for("admin.import_upload"))

        if batch.status == ImportBatch.STATUS_SUCCESS:
            flash(
                f"Import complete â€” {batch.rows_inserted} inserted, "
                f"{batch.rows_updated} updated"
                + (f", {batch.rows_closed} closed" if batch.rows_closed else "")
                + ".",
                "success",
            )
        else:
            flash(f"Import failed: {batch.error_message}", "danger")

        return redirect(url_for("admin.import_detail", batch_id=batch.id))

    return render_template("admin/import_upload.html", title="Upload CSV", form=form)


def run_importer(import_type: str, stream, filename: str, user_id: int) -> ImportBatch:
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

def handle_data_main_material():
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
