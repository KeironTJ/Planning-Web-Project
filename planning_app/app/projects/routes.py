"""Session-authenticated HTML and JSON endpoints; CSRF applies to both."""

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.exceptions import HTTPException, BadRequest, Conflict, NotFound
from werkzeug.datastructures import MultiDict

from app.auth.models import User
from app.extensions import db
from . import projects_bp as bp
from .models import LogEntry, Priority, Share, Status
from . import services as svc
from .navigation import return_to, validate_return_to
from .scheduling import schedule
from .dates import form_date, uk_date


@bp.before_request
def require_active_session():
    if not current_user.is_authenticated or not current_user.is_active:
        if request.path.startswith("/projects/api/"):
            return jsonify(error="Authentication required."), 401
        return redirect(url_for("auth.login", next=request.path))
    if not request.path.startswith("/projects/api/"):
        g.work_return_to = None
        value = (
            request.form.get("return_to")
            if request.method == "POST"
            else request.args.get("return_to")
        )
        g.work_return_to = validate_return_to(value)


@bp.errorhandler(HTTPException)
def http_error(error):
    db.session.rollback()
    if request.path.startswith("/projects/api/"):
        return jsonify(error=error.description), error.code
    return (
        render_template(
            "projects/error.html",
            title="Unable to complete request",
            message=error.description,
        ),
        error.code,
    )


@bp.errorhandler(StaleDataError)
def stale_error(_error):
    return http_error(Conflict("This item changed. Reload before saving."))


@bp.errorhandler(IntegrityError)
def integrity_error(_error):
    return http_error(Conflict("The change conflicts with database constraints."))


def payload():
    data = request.get_json()
    if not isinstance(data, dict):
        raise BadRequest("A JSON object is required.")
    return data


def users():
    return User.query.filter_by(is_active=True).order_by(User.username).all()


@bp.app_template_filter("work_kind")
def work_kind_label(kind):
    return svc.KIND_LABELS[kind]


@bp.app_template_filter("work_reference")
def reference_label(item):
    return svc.work_reference(item)


bp.add_app_template_filter(uk_date, "work_date")


@bp.app_template_global("work_can_quick_edit")
def can_quick_edit(kind, identifier):
    if kind not in svc.MODELS:
        return False
    permissions = request.environ.setdefault("work_quick_edit_permissions", {})
    key = (kind, identifier)
    if key not in permissions:
        item = db.session.get(svc.MODELS[kind], identifier)
        permissions[key] = (
            item is not None and svc.can_access(item, current_user, edit=True)
        )
    return permissions[key]


def options():
    active_users = users()
    return {
        "statuses": [s.value for s in Status],
        "priorities": [p.value for p in Priority],
        "users": active_users,
        "departments": sorted(
            {user.department for user in active_users if user.department}
        ),
    }


def paginate(query):
    page = svc.integer(request.args.get("page", 1), "page")
    per_page = svc.integer(request.args.get("per_page", 50), "per_page")
    if per_page > 100:
        raise BadRequest("per_page cannot exceed 100.")
    return query.paginate(page=page, per_page=per_page, error_out=False)


def page_links(pagination):
    args = request.args.to_dict()
    for field in ("page", *request.view_args):
        args.pop(field, None)
    return {
        "previous": (
            url_for(
                request.endpoint, **request.view_args, **args, page=pagination.prev_num
            )
            if pagination.has_prev
            else None
        ),
        "next": (
            url_for(
                request.endpoint, **request.view_args, **args, page=pagination.next_num
            )
            if pagination.has_next
            else None
        ),
    }


def json_page(pagination, serializer):
    return jsonify(
        items=[serializer(item) for item in pagination.items],
        page=pagination.page,
        per_page=pagination.per_page,
        total=pagination.total,
        pages=pagination.pages,
    )


@bp.get("/")
@login_required
def dashboard():
    sort_mode = svc.order_mode(request.args.get("sort"))
    visible = svc.visible_items(current_user, sort_mode)
    return render_template(
        "projects/dashboard.html",
        title="My work dashboard",
        report=svc.report(current_user),
        attention=svc.dashboard_attention(visible, current_user),
        hierarchy=svc.work_hierarchy(
            sum(visible.values(), []),
            current_user,
            include_children=False,
            sort_mode=sort_mode,
        ),
        root_orderable={
            kind: svc.can_reorder_root(kind, current_user)
            for kind in svc.MODELS
        },
        sort_mode=sort_mode,
        **options(),
    )


@bp.get("/reports")
@login_required
def reports():
    return render_template(
        "projects/reports.html",
        title="Work reports",
        report=svc.report(current_user),
        **options(),
    )


@bp.get("/logs")
@login_required
def logs():
    pagination = paginate(svc.visible_logs(current_user, request.args))
    visible = svc.visible_items(current_user)
    targets = sorted(
        [svc.serialize(item) for rows in visible.values() for item in rows],
        key=lambda item: (item["name"].casefold(), item["kind"], item["id"]),
    )[:100]
    return render_template(
        "projects/logs.html",
        title="Logs & updates",
        logs=[svc.serialize_log(log) for log in pagination.items],
        pagination=pagination,
        page_links=page_links(pagination),
        targets=targets,
        **options(),
    )


@bp.get("/<kind>")
@login_required
def listing(kind):
    if kind not in svc.MODELS:
        raise NotFound()
    views = {"list": "List", "hierarchy": "Hierarchy", "timeline": "Timeline"}
    if kind == "tasks":
        views["board"] = "Board"
    selected_view = request.args.get("view", "hierarchy")
    sort_mode = svc.order_mode(request.args.get("sort"))
    if selected_view not in views:
        raise BadRequest("Choose a valid work view: " + ", ".join(views) + ".")
    view_args = request.args.to_dict()
    view_args.pop("view", None)
    view_args.pop("kind", None)
    view_args.pop("return_to", None)
    pagination = paginate(svc.filtered_query(kind, current_user, request.args))
    quick_filters = dict(svc.QUICK_FILTERS)
    if kind == "tasks":
        quick_filters["assigned"] = "Assigned to me"
    quick_args = dict(view_args)
    quick_args.pop("page", None)
    quick_args.pop("quick", None)
    return render_template(
        "projects/list.html",
        title=kind.title(),
        kind=kind,
        selected_view=selected_view,
        quick_links={
            value: {
                "label": label,
                "url": url_for(
                    "projects.listing", kind=kind, view=selected_view,
                    quick="" if request.args.get("quick") == value else value,
                    **quick_args,
                ),
            }
            for value, label in quick_filters.items()
        },
        view_links={
            view: {
                "label": label,
                "url": url_for("projects.listing", kind=kind, view=view, **view_args),
            }
            for view, label in views.items()
        },
        items=[svc.serialize(item) for item in pagination.items],
        schedule=(
            schedule(
                [
                    row
                    for item in pagination.items
                    for row in [item, *svc.descendants(item)]
                ] if kind != "tasks" else pagination.items
            ) if selected_view == "timeline" else None
        ),
        hierarchy=(
            svc.work_hierarchy(
                pagination.items,
                current_user,
                include_children=kind != "tasks",
                sort_mode=sort_mode,
            )
            if selected_view == "hierarchy"
            else []
        ),
        pagination=pagination,
        sort_mode=sort_mode,
        page_links=page_links(pagination),
        **options(),
    )


@bp.get("/<kind>/<int:identifier>")
@login_required
def detail(kind, identifier):
    sort_mode = svc.order_mode(request.args.get("sort"))
    item = svc.get_item(kind, identifier, current_user)
    children = svc.descendants(item)
    top = svc.root(item)
    ancestors = []
    cursor = svc.parent(item)
    while cursor is not None:
        ancestors.append(svc.serialize(cursor))
        cursor = svc.parent(cursor)
    ancestors.reverse()
    shares = (
        Share.query.filter_by(**svc.target(top)).all()
        if svc.can_manage(top, current_user)
        else []
    )
    return render_template(
        "projects/detail.html",
        title=item.name,
        item=svc.serialize(item),
        hierarchy=svc.work_hierarchy(
            [item], current_user, include_ancestors=False, sort_mode=sort_mode
        ),
        ancestors=ancestors,
        children=[svc.serialize(row) for row in children],
        schedule=schedule([item, *children]),
        logs=[svc.serialize_log(log) for log in svc.timeline(item, current_user)][:100],
        progress=svc.progress([row for row in children if svc.kind_of(row) == "tasks"]),
        rollup=svc.financials([item] + children),
        completion=svc.completion_counts(children),
        status_actions=svc.status_actions(item, current_user),
        can_edit=svc.can_access(item, current_user, edit=True),
        can_manage=svc.can_manage(item, current_user),
        sort_mode=sort_mode,
        root=svc.serialize(top),
        shares=shares,
        **options(),
    )


def form_data(kind):
    fields = [
        "name",
        "description",
        "department",
        "owner_id",
        "priority",
        "start_date",
        "end_date",
        "deadline",
        "budget",
        "actuals",
    ]
    if request.form.get("version"):
        fields += ["version", "status"]
    if kind != "projects":
        fields.append("project_id")
    if kind == "tasks":
        fields.extend(["activity_id", "parent_task_id"])
    data = {field: request.form.get(field, "") for field in fields}
    for field in ("start_date", "end_date", "deadline"):
        data[field] = form_date(data[field], field)
    if kind == "tasks":
        data["assigned_user_ids"] = request.form.getlist("assigned_user_ids")
    if kind != "projects" and "parent" in request.form:
        data.update(parse_parent(kind, request.form["parent"]))
    return data


def parse_parent(kind, value):
    result = {"project_id": None}
    if kind == "tasks":
        result["activity_id"] = None
        result["parent_task_id"] = None
    if not value:
        return result
    parent_kind, separator, identifier = value.partition(":")
    allowed = {"projects": "project_id"}
    if kind == "tasks":
        allowed["activities"] = "activity_id"
        allowed["tasks"] = "parent_task_id"
    if not separator or parent_kind not in allowed:
        raise BadRequest("Choose a valid parent work item.")
    result[allowed[parent_kind]] = svc.integer(identifier, "parent")
    return result


@bp.route("/<kind>/new", methods=["GET", "POST"])
@bp.route("/<kind>/<int:identifier>/edit", methods=["GET", "POST"])
@login_required
def edit(kind, identifier=None):
    if kind not in svc.MODELS:
        raise NotFound()
    item = (
        svc.get_item(kind, identifier, current_user, edit=True) if identifier else None
    )
    if request.method == "POST":
        if request.form.get("form_action") == "preview":
            fields = {
                "name", "description", "department", "owner_id", "priority",
                "deadline", "parent", "version", "assigned_user_ids",
            }
            draft = MultiDict(
                (key, value)
                for key, value in request.form.items(multi=True)
                if key in fields
            )
            if item:
                draft.setdefault("version", str(item.version))
            return render_form(kind, item, draft)
        try:
            item = svc.save_item(kind, form_data(kind), current_user, item)
            db.session.commit()
        except HTTPException as error:
            db.session.rollback()
            flash(error.description, "danger")
            return render_form(kind, item, request.form), error.code
        flash(f"{svc.KIND_LABELS[kind].title()} saved.", "success")
        if identifier is None and request.form.get("save_action") == "another":
            return redirect(url_for(
                "projects.edit", kind=kind, return_to=return_to(),
                project_id=getattr(item, "project_id", None),
                activity_id=getattr(item, "activity_id", None),
                parent_task_id=getattr(item, "parent_task_id", None),
            ))
        return redirect(
            return_to() or url_for("projects.detail", kind=kind, identifier=item.id)
        )
    return render_form(kind, item)


def render_form(kind, item, submitted=None):
    visible = svc.visible_items(current_user)
    data = (
        svc.serialize(item)
        if item
        else {
            "owner_id": current_user.id,
            "department": current_user.department or "",
            "priority": "normal",
            "status": "planned",
            "budget": "0.00",
            "actuals": "0.00",
            "assigned_user_ids": [],
            "project_id": request.args.get("project_id", ""),
            "activity_id": request.args.get("activity_id", ""),
            "parent_task_id": request.args.get("parent_task_id", ""),
        }
    )
    if submitted is not None:
        data.update(submitted)
        data["assigned_user_ids"] = submitted.getlist("assigned_user_ids")
    elif not item:
        if sum(
            bool(data.get(field))
            for field in ("project_id", "activity_id", "parent_task_id")
        ) > 1:
            raise BadRequest("Choose only one parent work item.")
        for field, parent_kind in (
            ("project_id", "projects"),
            ("activity_id", "activities"),
            ("parent_task_id", "tasks"),
        ):
            if data.get(field):
                parent_item = svc.get_item(
                    parent_kind, svc.integer(data[field], field), current_user, edit=True
                )
                if parent_kind == "tasks" and (
                    parent_item.parent_task_id is not None
                    or parent_item.status in svc.TERMINAL
                ):
                    raise BadRequest("Choose an open, top-level task as the parent.")
                data["department"] = parent_item.department
    selected_parent = data.get("parent")
    if selected_parent is None:
        selected_parent = (
            f"activities:{data['activity_id']}" if data.get("activity_id")
            else f"tasks:{data['parent_task_id']}" if data.get("parent_task_id")
            else f"projects:{data['project_id']}" if data.get("project_id") else ""
        )
    return render_template(
        "projects/form.html",
        title=f"{'Edit' if item else 'New'} {svc.KIND_LABELS[kind]}",
        kind=kind,
        item=data,
        selected_parent=selected_parent,
        projects=[
            row
            for row in visible["projects"]
            if svc.can_access(row, current_user, True)
        ],
        parent_tasks=[
            row
            for row in visible["tasks"]
            if row.parent_task_id is None
            and row.status not in svc.TERMINAL
            and (item is None or row.id != item.id)
            and svc.can_access(row, current_user, True)
        ] if item is not None or selected_parent.startswith("tasks:") else [],
        activities=[
            row
            for row in visible["activities"]
            if svc.can_access(row, current_user, True)
        ],
        **options(),
    )


@bp.get("/timeline")
@login_required
def scheduling_timeline():
    visible = svc.visible_items(current_user)
    return render_template(
        "projects/schedule.html", title="Work scheduling timeline",
        schedule=schedule(sum(visible.values(), [])), **options(),
    )


@bp.get("/api/<kind>/create-options")
def create_options(kind):
    if kind not in svc.MODELS:
        raise NotFound()
    visible = svc.visible_items(current_user)
    defaults = {
        "owner_id": current_user.id, "department": current_user.department or "",
        "priority": "normal", "parent": "",
    }
    if kind != "projects":
        for field, parent_kind in (
            ("activity_id", "activities"),
            ("project_id", "projects"),
            ("parent_task_id", "tasks"),
        ):
            if request.args.get(field):
                if parent_kind == "activities" and kind != "tasks":
                    raise BadRequest("Only tasks can belong to activities.")
                if parent_kind == "tasks" and kind != "tasks":
                    raise BadRequest("Only tasks can belong to tasks.")
                if defaults["parent"]:
                    raise BadRequest("Choose a project OR an activity, not both.")
                parent_item = svc.get_item(
                    parent_kind, svc.integer(request.args[field], field),
                    current_user, edit=True,
                )
                if parent_kind == "tasks" and (
                    parent_item.parent_task_id is not None
                    or parent_item.status in svc.TERMINAL
                ):
                    raise BadRequest("Choose an open, top-level task as the parent.")
                defaults.update(
                    parent=f"{parent_kind}:{parent_item.id}",
                    department=parent_item.department,
                )
    return jsonify(
        defaults=defaults,
        parents=[
            {"value": f"{parent_kind}:{row.id}",
             "label": f"{svc.work_reference(row)} - {row.name}",
             "department": row.department}
            for parent_kind in (
                ["projects", "activities", "tasks"] if kind == "tasks"
                else ["projects"] if kind == "activities" else []
            )
            for row in visible[parent_kind]
            if svc.can_access(row, current_user, edit=True)
            and row.status not in svc.TERMINAL
            and (parent_kind != "tasks" or row.parent_task_id is None)
        ],
        users=[{"id": user.id, "name": user.full_name} for user in users()],
        priorities=[priority.value for priority in Priority],
    )


@bp.post("/<kind>/<int:identifier>/delete")
@login_required
def delete(kind, identifier):
    item = svc.get_item(kind, identifier, current_user, edit=True)
    svc.delete_item(item, request.form, current_user)
    db.session.commit()
    flash("Item deleted; audit history retained.", "success")
    return redirect(url_for("projects.listing", kind=kind))


@bp.post("/<kind>/<int:identifier>/status")
@login_required
def change_status(kind, identifier):
    item = svc.get_item(kind, identifier, current_user, edit=True)
    try:
        svc.save_item(
            kind,
            {
                "version": request.form.get("version"),
                "status": request.form.get("status"),
            },
            current_user,
            item,
        )
    except HTTPException as error:
        db.session.rollback()
        if error.code != 409 or not error.description.startswith(
            "Complete or cancel child work"
        ):
            raise
        flash(error.description, "warning")
        return redirect(
            return_to() or url_for("projects.detail", kind=kind, identifier=identifier)
        )
    db.session.commit()
    flash(f"Status changed to {item.status.value}.", "success")
    return redirect(
        return_to() or url_for("projects.detail", kind=kind, identifier=identifier)
    )


@bp.post("/<kind>/<int:identifier>/shares")
@login_required
def share(kind, identifier):
    item = svc.get_item(kind, identifier, current_user)
    svc.save_share(
        item,
        {field: request.form.get(field) for field in ("user_id", "department", "role")},
        current_user,
    )
    db.session.commit()
    return redirect(
        return_to() or url_for("projects.detail", kind=kind, identifier=identifier)
    )


@bp.post("/<kind>/<int:identifier>/shares/<int:share_id>/delete")
@login_required
def unshare(kind, identifier, share_id):
    item = svc.get_item(kind, identifier, current_user)
    svc.revoke_share(item, share_id, current_user)
    db.session.commit()
    return redirect(
        return_to() or url_for("projects.detail", kind=kind, identifier=identifier)
    )


@bp.post("/logs")
@login_required
def add_log():
    svc.save_log(
        {
            key: value
            for key, value in request.form.items()
            if key not in {"csrf_token", "return_to"}
        },
        current_user,
    )
    db.session.commit()
    kind, identifier = next(
        (kind, request.form[field])
        for kind, field in (
            ("projects", "project_id"),
            ("activities", "activity_id"),
            ("tasks", "task_id"),
        )
        if request.form.get(field)
    )
    return redirect(
        return_to() or url_for("projects.detail", kind=kind, identifier=identifier)
    )


@bp.post("/logs/<int:identifier>/<action>")
@login_required
def change_log(identifier, action):
    if action not in ("edit", "delete"):
        raise NotFound()
    log = db.session.get(LogEntry, identifier)
    if log is None:
        raise NotFound()
    svc.save_log(
        {
            key: value
            for key, value in request.form.items()
            if key not in {"csrf_token", "return_to"}
        },
        current_user,
        log,
        delete=action == "delete",
    )
    db.session.commit()
    item = svc.log_target(log)
    return redirect(
        return_to()
        or url_for("projects.detail", kind=svc.kind_of(item), identifier=item.id)
    )


@bp.get("/api/reports")
def api_reports():
    return jsonify(svc.report(current_user))


@bp.get("/api/<kind>/<int:identifier>/edit-options")
def work_edit_options(kind, identifier):
    item = svc.get_item(kind, identifier, current_user, edit=True)
    top = svc.root(item)
    return jsonify(
        item=svc.serialize(item),
        priorities=[priority.value for priority in Priority],
        assignees=(
            [
                {"id": user.id, "name": user.full_name}
                for user in users()
                if svc.can_access(top, user)
            ]
            if kind == "tasks"
            else []
        ),
    )


@bp.route("/api/logs", methods=["GET", "POST"])
def api_logs():
    if request.method == "POST":
        log = svc.save_log(payload(), current_user)
        db.session.commit()
        return jsonify(svc.serialize_log(log)), 201
    return json_page(
        paginate(svc.visible_logs(current_user, request.args)), svc.serialize_log
    )


@bp.get("/archive")
@login_required
def archive():
    items = [
        svc.serialize(item)
        for cls in svc.MODELS.values()
        for item in cls.query.filter(cls.deleted_at.is_not(None)).all()
        if svc.can_manage(item, current_user)
    ]
    return render_template(
        "projects/archive.html", title="Retained work history", items=items, **options()
    )


def archived_item(kind, identifier):
    if kind not in svc.MODELS:
        raise NotFound()
    item = db.session.get(svc.MODELS[kind], identifier)
    if item is None or not svc.can_manage(item, current_user):
        raise NotFound()
    return item


@bp.get("/<kind>/<int:identifier>/history")
@login_required
def history(kind, identifier):
    item = archived_item(kind, identifier)
    return render_template(
        "projects/history.html",
        title=f"History: {item.name}",
        item=svc.serialize(item),
        logs=[
            svc.serialize_log(log)
            for log in svc.timeline(item, current_user, archive=True)
        ],
        **options(),
    )


@bp.get("/api/<kind>/<int:identifier>/history")
def api_history(kind, identifier):
    item = archived_item(kind, identifier)
    return jsonify(
        item=svc.serialize(item),
        timeline=[
            svc.serialize_log(log)
            for log in svc.timeline(item, current_user, archive=True)
        ],
    )


@bp.route("/api/logs/<int:identifier>", methods=["GET", "PATCH", "DELETE"])
def api_log(identifier):
    log = db.session.get(LogEntry, identifier)
    if (
        log is None
        or log.deleted_at
        or not svc.can_access(svc.log_target(log), current_user)
    ):
        raise NotFound()
    if request.method != "GET":
        svc.save_log(payload(), current_user, log, delete=request.method == "DELETE")
        db.session.commit()
    return jsonify(svc.serialize_log(log))


@bp.route("/api/<kind>", methods=["GET", "POST"])
def api_collection(kind):
    if kind not in svc.MODELS:
        raise NotFound()
    if request.method == "POST":
        item = svc.save_item(kind, payload(), current_user)
        db.session.commit()
        return jsonify(svc.serialize(item)), 201
    return json_page(
        paginate(svc.filtered_query(kind, current_user, request.args)), svc.serialize
    )


@bp.route("/api/<kind>/<int:identifier>", methods=["GET", "PATCH", "DELETE"])
def api_item(kind, identifier):
    item = svc.get_item(kind, identifier, current_user, edit=request.method != "GET")
    if request.method == "PATCH":
        svc.save_item(kind, payload(), current_user, item)
        db.session.commit()
    elif request.method == "DELETE":
        svc.delete_item(item, payload(), current_user)
        db.session.commit()
    return jsonify(svc.serialize(item))


@bp.post("/api/order")
def api_work_order():
    items = svc.reorder_work(payload(), current_user)
    db.session.commit()
    return jsonify(items=[svc.serialize(item) for item in items])


@bp.get("/api/<kind>/<int:identifier>/summary")
def api_summary(kind, identifier):
    item = svc.get_item(kind, identifier, current_user)
    children = svc.descendants(item)
    return jsonify(
        item=svc.serialize(item),
        progress=svc.progress([row for row in children if svc.kind_of(row) == "tasks"]),
        children=[svc.serialize(row) for row in children],
        rollup=svc.financials([item] + children),
        completion=svc.completion_counts(children),
        timeline=[svc.serialize_log(log) for log in svc.timeline(item, current_user)],
    )


@bp.route("/api/<kind>/<int:identifier>/shares", methods=["GET", "POST"])
def api_shares(kind, identifier):
    item = svc.get_item(kind, identifier, current_user)
    if svc.parent(item) is not None or not svc.can_manage(item, current_user):
        abort(403)
    if request.method == "POST":
        svc.save_share(item, payload(), current_user)
        db.session.commit()
    return jsonify(
        items=[
            {
                "id": share.id,
                "user_id": share.user_id,
                "department": share.department,
                "role": share.role,
            }
            for share in Share.query.filter_by(**svc.target(item)).all()
        ]
    )


@bp.delete("/api/<kind>/<int:identifier>/shares/<int:share_id>")
def api_unshare(kind, identifier, share_id):
    item = svc.get_item(kind, identifier, current_user)
    svc.revoke_share(item, share_id, current_user)
    db.session.commit()
    return "", 204
