"""Materials blueprint routes — Phase 6."""

from datetime import date, timedelta

from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from . import materials_bp
from .services.class_labels import (
    delete_class_label,
    get_class_label_map,
    get_class_labels,
    set_class_label,
)
from .services.dashboard import get_weekly_availability_summary, get_weekly_so_breakdown
from .services.exempt import add_exemptions, get_exempt_materials, remove_exemptions
from .services.insights import get_shortage_insights
from .services.netting import get_shortage_report
from .services.pegging import get_mrp_pegging
from .services.stock import get_po_list, get_stock_list, get_stock_overview, get_stock_summary
from .services.types import MAT_STATUS_META, WO_STATUS_META, wo_status
from app.extensions import db
from app.sales.orders.models import Department
from app.core.decorators import permission_required


_AT_RISK_STATUSES = {"high_risk", "late_po", "med_risk", "low_risk"}


def _class_options(rows: list, class_labels: dict[str, str] | None = None) -> list[dict]:
    """Distinct material classes present in the at-risk rows, with counts, for filter dropdowns."""
    class_labels = class_labels or {}
    counts: dict[str, int] = {}
    for r in rows:
        if r.status in _AT_RISK_STATUSES:
            cid = r.class_id or "Unassigned"
            counts[cid] = counts.get(cid, 0) + 1
    return sorted(
        (
            {"class_id": cid, "label": class_labels.get(cid, cid), "count": n}
            for cid, n in counts.items()
        ),
        key=lambda c: c["label"],
    )


def _wo_status_options(rows: list) -> list[dict]:
    """Distinct work-order statuses present in the at-risk rows, with counts, for filter dropdowns."""
    counts: dict[str, int] = {}
    for r in rows:
        if r.status in _AT_RISK_STATUSES:
            key = wo_status(r.job_released, r.job_firm)
            counts[key] = counts.get(key, 0) + 1
    return [
        {"key": key, "label": WO_STATUS_META[key][0], "count": counts[key]}
        for key in WO_STATUS_META
        if key in counts
    ]


def _status_counts(rows: list) -> dict:
    """Count at-risk rows per status tier — independent of any status filter, so the
    KPI filter cards always show totals for the whole class/dept/date/search scope."""
    counts: dict[str, int] = {}
    for r in rows:
        if r.status in _AT_RISK_STATUSES:
            counts[r.status] = counts.get(r.status, 0) + 1
    return counts



@materials_bp.route("/")
@login_required
@permission_required("view_materials")
def index():
    summary      = get_stock_summary()
    weekly       = get_weekly_availability_summary(weeks_ahead=12)
    so_breakdown = get_weekly_so_breakdown(weeks_ahead=12)
    return render_template(
        "materials/index.html",
        title="Fabric and Hide Availability",
        summary=summary,
        weekly=weekly,
        so_breakdown=so_breakdown,
        timedelta=timedelta,
        today=date.today(),
    )


@materials_bp.route("/shortage")
@login_required
@permission_required("view_materials")
def shortage():
    source       = request.args.get("source", "all")
    dept_filter  = request.args.get("dept", "")
    search       = request.args.get("q", "")
    status_filter = request.args.get("status", "")  # "" = all at-risk
    cls_filter   = request.args.get("cls", "")       # "" = all material classes
    wo_status_filter = request.args.get("wo_status", "")  # "" = all work-order statuses
    due_before_str = request.args.get("due_before", "")
    due_from_str   = request.args.get("due_from", "")

    due_before = None
    if due_before_str:
        try:
            due_before = date.fromisoformat(due_before_str)
        except ValueError:
            pass

    due_from = None
    if due_from_str:
        try:
            due_from = date.fromisoformat(due_from_str)
        except ValueError:
            pass

    # Always fetch all rows (before class/status filtering) so class options and the
    # "clear this filter" experience reflect the full picture for the current dept/date/search scope
    data = get_shortage_report(
        source=source,
        dept_filter=dept_filter or None,
        search=search or None,
        shortages_only=False,
        due_before=due_before,
        due_from=due_from,
    )

    class_labels = get_class_label_map()
    class_options = _class_options(data["rows"], class_labels)
    _valid_cls = cls_filter if cls_filter in {c["class_id"] for c in class_options} else ""
    if _valid_cls:
        data["rows"] = [r for r in data["rows"] if (r.class_id or "Unassigned") == _valid_cls]

    wo_status_options = _wo_status_options(data["rows"])
    _valid_wo_status = wo_status_filter if wo_status_filter in WO_STATUS_META else ""
    if _valid_wo_status:
        data["rows"] = [r for r in data["rows"] if wo_status(r.job_released, r.job_firm) == _valid_wo_status]

    # KPI card counts always reflect the full class/dept/date/search scope (all tiers),
    # independent of the status filter below, so the cards keep working as filter buttons.
    status_counts_all = _status_counts(data["rows"])

    # Filter display rows: all at-risk by default, or a specific status tier
    _valid_status = status_filter if status_filter in _AT_RISK_STATUSES else ""
    if _valid_status:
        data["rows"] = [r for r in data["rows"] if r.status == _valid_status]
    else:
        data["rows"] = [r for r in data["rows"] if r.status in _AT_RISK_STATUSES]
    data["total_rows"] = len(data["rows"])

    # Charts/summary computed on the same rows as the detail table, so they reflect
    # every active filter including the status tier.
    shortage_insights = get_shortage_insights(data["rows"], class_labels=class_labels)

    departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()

    return render_template(
        "materials/shortage.html",
        title="Material Shortage Report",
        data=data,
        shortage_insights=shortage_insights,
        status_counts_all=status_counts_all,
        departments=departments,
        class_options=class_options,
        class_labels=class_labels,
        wo_status_options=wo_status_options,
        wo_status_meta=WO_STATUS_META,
        wo_status_of=wo_status,
        mat_status_meta=MAT_STATUS_META,
        source=source,
        dept_filter=dept_filter,
        search=search,
        status_filter=_valid_status,
        cls_filter=_valid_cls,
        wo_status_filter=_valid_wo_status,
        due_before=due_before_str,
        due_from=due_from_str,
        today=date.today(),
    )


@materials_bp.route("/component-shortage")
@login_required
@permission_required("view_materials")
def component_shortage():
    """Component availability shortage report (sourced from PlanningMatReqComp)."""
    dept_filter   = request.args.get("dept", "")
    search        = request.args.get("q", "")
    status_filter = request.args.get("status", "")
    cls_filter    = request.args.get("cls", "")
    wo_status_filter = request.args.get("wo_status", "")  # "" = all work-order statuses
    due_before_str = request.args.get("due_before", "")
    due_from_str   = request.args.get("due_from", "")
    so_filter      = request.args.get("so", "")

    due_before = None
    if due_before_str:
        try:
            due_before = date.fromisoformat(due_before_str)
        except ValueError:
            pass

    due_from = None
    if due_from_str:
        try:
            due_from = date.fromisoformat(due_from_str)
        except ValueError:
            pass

    data = get_shortage_report(
        material_group="component",
        dept_filter=dept_filter or None,
        search=search or None,
        so_filter=so_filter or None,
        shortages_only=False,
        due_before=due_before,
        due_from=due_from,
    )

    class_labels = get_class_label_map()
    class_options = _class_options(data["rows"], class_labels)
    _valid_cls = cls_filter if cls_filter in {c["class_id"] for c in class_options} else ""
    if _valid_cls:
        data["rows"] = [r for r in data["rows"] if (r.class_id or "Unassigned") == _valid_cls]

    wo_status_options = _wo_status_options(data["rows"])
    _valid_wo_status = wo_status_filter if wo_status_filter in WO_STATUS_META else ""
    if _valid_wo_status:
        data["rows"] = [r for r in data["rows"] if wo_status(r.job_released, r.job_firm) == _valid_wo_status]

    # KPI card counts always reflect the full class/dept/date/search scope (all tiers),
    # independent of the status filter below, so the cards keep working as filter buttons.
    status_counts_all = _status_counts(data["rows"])

    _valid_status = status_filter if status_filter in _AT_RISK_STATUSES else ""
    if _valid_status:
        data["rows"] = [r for r in data["rows"] if r.status == _valid_status]
    else:
        data["rows"] = [r for r in data["rows"] if r.status in _AT_RISK_STATUSES]
    data["total_rows"] = len(data["rows"])

    # Charts/summary computed on the same rows as the detail table, so they reflect
    # every active filter including the status tier.
    shortage_insights = get_shortage_insights(data["rows"], class_labels=class_labels)

    departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()

    return render_template(
        "materials/component_shortage.html",
        title="Component Shortage Report",
        data=data,
        shortage_insights=shortage_insights,
        status_counts_all=status_counts_all,
        departments=departments,
        class_options=class_options,
        class_labels=class_labels,
        wo_status_options=wo_status_options,
        wo_status_meta=WO_STATUS_META,
        wo_status_of=wo_status,
        mat_status_meta=MAT_STATUS_META,
        dept_filter=dept_filter,
        search=search,
        status_filter=_valid_status,
        cls_filter=_valid_cls,
        wo_status_filter=_valid_wo_status,
        due_before=due_before_str,
        due_from=due_from_str,
        today=date.today(),
    )


@materials_bp.route("/stock")
@login_required
@permission_required("view_materials")
def stock_list():
    search = request.args.get("q", "")
    class_filter = request.args.get("cls", "")
    page   = request.args.get("page", 1, type=int)
    stock  = get_stock_list(search=search or None, page=page, class_filter=class_filter or None)
    overview = get_stock_overview()
    return render_template(
        "materials/stock_list.html",
        title="Stock On Hand",
        stock=stock,
        overview=overview,
        search=search,
        class_filter=class_filter,
    )


@materials_bp.route("/purchase-orders")
@login_required
@permission_required("view_materials")
def po_list():
    search      = request.args.get("q", "")
    due_from_s  = request.args.get("due_from", "")
    due_before_s = request.args.get("due_before", "")
    page        = request.args.get("page", 1, type=int)
    today       = date.today()

    due_from = None
    if due_from_s:
        try:
            due_from = date.fromisoformat(due_from_s)
        except ValueError:
            pass

    due_before = None
    if due_before_s:
        try:
            due_before = date.fromisoformat(due_before_s)
        except ValueError:
            pass

    pos = get_po_list(
        search=search or None,
        due_from=due_from,
        due_before=due_before,
        page=page,
    )
    from app.purchasing.materials.models import PurchaseOrder
    last_po = PurchaseOrder.query.order_by(PurchaseOrder.imported_at.desc()).first()
    return render_template(
        "materials/po_list.html",
        title="Open Purchase Orders",
        pos=pos,
        search=search,
        due_from=due_from_s,
        due_before=due_before_s,
        today=today,
        last_imported=last_po.imported_at if last_po else None,
    )


@materials_bp.route("/main-requirements")
@login_required
@permission_required("view_materials")
def main_requirements():
    from app.purchasing.materials.models import MaterialRequirementMain
    from sqlalchemy import distinct
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
    depts = [r[0] for r in db.session.query(distinct(MaterialRequirementMain.warehouse_code)).filter(MaterialRequirementMain.warehouse_code.isnot(None)).order_by(MaterialRequirementMain.warehouse_code).all()]
    return render_template(
        "materials/main_requirements.html",
        title="Main Material Requirements",
        rows=rows, q=q, f_dept=f_dept, depts=depts, total=total,
        last_imported=last.imported_at if last else None,
    )


@materials_bp.route("/exempt", methods=["GET"])
@login_required
@permission_required("manage_imports", "manage_purchasing")
def exempt_materials():
    search = request.args.get("q", "").strip()
    items = get_exempt_materials(search=search or None)
    return render_template(
        "materials/exempt_materials.html",
        title="MRP Exempt Materials",
        items=items,
        search=search,
    )


@materials_bp.route("/exempt/add", methods=["POST"])
@login_required
@permission_required("manage_imports", "manage_purchasing")
def exempt_add():
    raw_codes = request.form.get("codes", "")
    reason = request.form.get("reason", "")
    # Accept newline- or comma-separated codes
    codes = [c for part in raw_codes.replace(",", "\n").splitlines() for c in [part.strip()] if c]
    if not codes:
        flash("No material codes entered.", "warning")
        return redirect(url_for("materials.exempt_materials"))
    result = add_exemptions(codes, reason=reason or None, user_id=current_user.id)
    flash(
        f"{result['added']} material{'s' if result['added'] != 1 else ''} added to exempt list"
        + (f" ({result['skipped']} already exempt)" if result["skipped"] else "") + ".",
        "success" if result["added"] else "info",
    )
    return redirect(url_for("materials.exempt_materials"))


@materials_bp.route("/exempt/remove", methods=["POST"])
@login_required
@permission_required("manage_imports", "manage_purchasing")
def exempt_remove_bulk():
    raw_codes = request.form.get("codes", "")
    codes = [c for part in raw_codes.replace(",", "\n").splitlines() for c in [part.strip()] if c]
    if not codes:
        flash("No material codes entered.", "warning")
        return redirect(url_for("materials.exempt_materials"))
    deleted = remove_exemptions(codes)
    flash(
        f"{deleted} material{'s' if deleted != 1 else ''} removed from exempt list.",
        "success" if deleted else "info",
    )
    return redirect(url_for("materials.exempt_materials"))


@materials_bp.route("/exempt/<string:code>/delete", methods=["POST"])
@login_required
@permission_required("manage_imports", "manage_purchasing")
def exempt_delete(code):
    deleted = remove_exemptions([code])
    if deleted:
        flash(f"{code} removed from exempt list.", "success")
    else:
        flash(f"{code} not found in exempt list.", "warning")
    return redirect(url_for("materials.exempt_materials"))


@materials_bp.route("/class-labels", methods=["GET"])
@login_required
@permission_required("manage_imports", "manage_purchasing")
def class_labels():
    items = get_class_labels()
    return render_template(
        "materials/class_labels.html",
        title="Material Class Labels",
        items=items,
    )


@materials_bp.route("/class-labels/save", methods=["POST"])
@login_required
@permission_required("manage_imports", "manage_purchasing")
def class_labels_save():
    class_id = request.form.get("class_id", "")
    label = request.form.get("label", "")
    row = set_class_label(class_id, label)
    if row:
        flash(f"Label saved for class {row.class_id}.", "success")
    elif class_id.strip():
        flash(f"Label removed for class {class_id.strip()} (blank label).", "info")
    else:
        flash("Please enter a class ID.", "warning")
    return redirect(url_for("materials.class_labels"))


@materials_bp.route("/class-labels/<string:class_id>/delete", methods=["POST"])
@login_required
@permission_required("manage_imports", "manage_purchasing")
def class_labels_delete(class_id):
    deleted = delete_class_label(class_id)
    if deleted:
        flash(f"Label removed for class {class_id}.", "success")
    else:
        flash(f"No label found for class {class_id}.", "warning")
    return redirect(url_for("materials.class_labels"))


@materials_bp.route("/mrp")
@login_required
@permission_required("view_materials")
def mrp():
    search = request.args.get("q", "").strip()
    so_number = request.args.get("so", "").strip()
    material_group = request.args.get("material_group", "all").strip().lower()
    if material_group not in {"all", "fabric", "component"}:
        material_group = "all"
    data = get_mrp_pegging(
        search=search or None,
        so_number=so_number or None,
        material_group=material_group,
    )
    return render_template(
        "materials/mrp.html",
        title="MRP Pegging",
        data=data,
        search=search,
        so_number=so_number,
        material_group=material_group,
        mat_status_meta=MAT_STATUS_META,
    )
