"""Validation, access control, atomic mutations and visibility-safe reporting."""

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound

from app.auth.models import AuditLog, User
from app.extensions import db
from .models import Activity, LogEntry, Priority, Project, Share, Status, Task, utcnow

MODELS = {"projects": Project, "activities": Activity, "tasks": Task}
KIND_LABELS = {"projects": "project", "activities": "activity", "tasks": "task"}
TERMINAL = {Status.COMPLETED, Status.CANCELLED}
QUICK_FILTERS = {
    "mine": "Owned by me",
    "open": "Open",
    "blocked": "Blocked",
    "overdue": "Overdue",
    "upcoming": "Due soon (14 days)",
}
TRANSITIONS = {
    Status.PLANNED: {Status.ACTIVE, Status.CANCELLED},
    Status.ACTIVE: {Status.BLOCKED, Status.COMPLETED, Status.CANCELLED},
    Status.BLOCKED: {Status.ACTIVE, Status.CANCELLED},
    Status.COMPLETED: {Status.ACTIVE},
    Status.CANCELLED: {Status.PLANNED},
}
STATUS_ACTION_LABELS = {
    Status.PLANNED: "Restart",
    Status.ACTIVE: "Start / resume",
    Status.BLOCKED: "Mark blocked",
    Status.COMPLETED: "Complete",
    Status.CANCELLED: "Cancel / close",
}


def status_actions(item, user):
    if not can_access(item, user, edit=True):
        return []
    actions = []
    for status in Status:
        if status not in TRANSITIONS[item.status]:
            continue
        label = STATUS_ACTION_LABELS[status]
        if status == Status.ACTIVE and item.status == Status.COMPLETED:
            label = "Reopen"
        elif status == Status.ACTIVE and item.status == Status.PLANNED:
            label = "Start"
        actions.append({"status": status.value, "label": label})
    return actions


def kind_of(item):
    return next(key for key, cls in MODELS.items() if isinstance(item, cls))


def parent(item):
    if isinstance(item, Task):
        return item.parent_task or item.activity or item.project
    if isinstance(item, Activity):
        return item.project
    return None


def root(item):
    while parent(item) is not None:
        item = parent(item)
    return item


def target(item):
    return {
        {"projects": "project_id", "activities": "activity_id", "tasks": "task_id"}[
            kind_of(item)
        ]: item.id
    }


def can_manage(item, user):
    item = root(item)
    return user.is_active and (
        user.is_admin or user.id in (item.owner_id, item.created_by)
    )


def can_access(item, user, edit=False):
    if not user.is_active:
        return False
    cursor = item
    while cursor is not None:
        if cursor.deleted_at is not None:
            return False
        cursor = parent(cursor)
    top = root(item)
    if can_manage(top, user):
        return True
    if top.id is None:
        return False
    shares = Share.query.filter_by(**target(top)).all()
    return any(
        (
            share.user_id == user.id
            or (share.department is not None and share.department == user.department)
        )
        and (not edit or share.role == "editor")
        for share in shares
    )


def get_item(kind, identifier, user, edit=False):
    if kind not in MODELS:
        raise NotFound()
    item = db.session.get(MODELS[kind], identifier)
    if item is None or not can_access(item, user):
        raise NotFound()
    if edit and not can_access(item, user, edit=True):
        raise Forbidden("Editor access is required.")
    return item


def integer(value, field):
    if isinstance(value, bool):
        raise BadRequest(f"{field} must be an integer.")
    try:
        result = int(str(value))
    except (ValueError, TypeError):
        raise BadRequest(f"{field} must be an integer.") from None
    if not 1 <= result <= 2147483647:
        raise BadRequest(f"{field} must be between 1 and 2147483647.")
    return result


def active_user(value):
    identifier = integer(value, "user_id")
    user = db.session.get(User, identifier)
    if user is None or not user.is_active:
        raise BadRequest("Select an active user.")
    return user


def text(value, field, limit, required=False):
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise BadRequest(f"{field} must be text of at most {limit} characters.")
    value = value.strip()
    if required and not value:
        raise BadRequest(f"{field} is required.")
    return value


def iso_timestamp(value):
    if value is None:
        return None
    # SQLite drops timezone offsets; these audit columns are always written in UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def work_reference(item):
    """Location path uses record IDs, never filtered-list positions."""
    number = f"{item.id:02}"
    if isinstance(item, Project):
        return number
    if isinstance(item, Activity):
        return f"{item.project_id:02}.{number}" if item.project_id else f"A{number}"
    if item.activity:
        return f"{work_reference(item.activity)}.{number}"
    if item.project_id:
        return f"{item.project_id:02}.00.{number}"
    return f"T{number}"


def financials(items):
    budget = sum((item.budget for item in items), Decimal("0.00"))
    actuals = sum((item.actuals for item in items), Decimal("0.00"))
    return {
        "budget": str(budget),
        "actuals": str(actuals),
        "variance": str(budget - actuals),
        "over_budget": actuals > budget,
    }


def serialize(item):
    result = {
        "id": item.id,
        "kind": kind_of(item),
        "reference": work_reference(item),
        "name": item.name,
        "description": item.description,
        "owner_id": item.owner_id,
        "department": item.department,
        "status": item.status.value,
        "priority": item.priority.value,
        "budget": str(item.budget),
        "actuals": str(item.actuals),
        "variance": str(item.variance),
        "over_budget": item.actuals > item.budget,
        "created_by": item.created_by,
        "version": item.version,
    }
    for field in (
        "start_date",
        "end_date",
        "deadline",
        "created_at",
        "updated_at",
        "deleted_at",
    ):
        value = getattr(item, field)
        result[field] = (
            iso_timestamp(value)
            if isinstance(value, datetime)
            else (value.isoformat() if value else None)
        )
    result["project_id"] = getattr(item, "project_id", None)
    result["activity_id"] = getattr(item, "activity_id", None)
    result["parent_task_id"] = getattr(item, "parent_task_id", None)
    result["effective_project_id"] = (
        root(item).id if isinstance(root(item), Project) else None
    )
    result["assigned_user_ids"] = (
        [user.id for user in item.assigned_users] if isinstance(item, Task) else []
    )
    return result


def audit(item, user, action, before=None, after=None):
    details = json.dumps({"before": before, "after": after}, sort_keys=True)
    db.session.add(
        AuditLog(
            user_id=user.id,
            action=f"projects.{action}",
            resource=f"{kind_of(item)}:{item.id}",
            details=details,
        )
    )
    db.session.add(
        LogEntry(
            **target(item),
            created_by=user.id,
            kind="audit",
            body=json.dumps(
                {"action": action, "before": before, "after": after}, sort_keys=True
            ),
        )
    )


def check_version(item, data):
    if integer(data.get("version"), "version") != item.version:
        raise Conflict("This item changed. Reload before saving.")


def save_item(kind, data, user, item=None):
    if kind not in MODELS:
        raise NotFound()
    fields = {
        "name",
        "description",
        "owner_id",
        "department",
        "status",
        "priority",
        "start_date",
        "end_date",
        "deadline",
        "budget",
        "actuals",
        "version",
    }
    if kind != "projects":
        fields.add("project_id")
    if kind == "tasks":
        fields.update({"activity_id", "parent_task_id", "assigned_user_ids"})
    if set(data) - fields:
        raise BadRequest("Unknown fields: " + ", ".join(sorted(set(data) - fields)))
    creating = item is None
    if creating:
        item = MODELS[kind](
            owner_id=user.id,
            created_by=user.id,
            description="",
            department=user.department or "",
            status=Status.PLANNED,
            priority=Priority.NORMAL,
            budget=Decimal("0.00"),
            actuals=Decimal("0.00"),
        )
    else:
        if not can_access(item, user, edit=True):
            raise Forbidden()
        check_version(item, data)
    before = None if creating else serialize(item)
    manager = creating or can_manage(item, user)
    for field in ("name", "description", "department"):
        if field in data:
            setattr(
                item,
                field,
                text(
                    data[field],
                    field,
                    (
                        10000
                        if field == "description"
                        else 200 if field == "name" else 100
                    ),
                    field != "description",
                ),
            )
    if not item.name or not item.department:
        raise BadRequest("Name and department are required.")
    if "owner_id" in data:
        owner = active_user(data["owner_id"])
        if not manager and owner.id != item.owner_id:
            raise Forbidden("Only the owner or creator can transfer ownership.")
        item.owner_id = owner.id
    for field, cls in (("status", Status), ("priority", Priority)):
        if field in data:
            try:
                value = cls(data[field])
            except (ValueError, TypeError):
                raise BadRequest(f"Invalid {field}.") from None
            if field == "status" and value != item.status:
                if creating and value != Status.PLANNED:
                    raise BadRequest("New items must start as planned.")
                if value not in TRANSITIONS[item.status]:
                    raise BadRequest(
                        f"Cannot change {item.status.value} to {value.value}."
                    )
            setattr(item, field, value)
    for field in ("start_date", "end_date", "deadline"):
        if field in data:
            raw = data[field]
            if raw is not None and not isinstance(raw, str):
                raise BadRequest(f"{field} must be an ISO date or null.")
            try:
                value = date.fromisoformat(raw) if raw else None
            except (ValueError, TypeError):
                raise BadRequest(f"{field} must be an ISO date.") from None
            setattr(item, field, value)
    if item.start_date:
        if item.end_date and item.end_date < item.start_date:
            raise BadRequest("End date cannot precede start date.")
        if item.deadline and item.deadline < item.start_date:
            raise BadRequest("Deadline cannot precede start date.")
    for field in ("budget", "actuals"):
        if field in data:
            try:
                value = Decimal(str(data[field]))
                valid = value.is_finite() and 0 <= value <= Decimal("999999999999.99")
                valid = valid and value == value.quantize(Decimal("0.01"))
            except InvalidOperation:
                valid = False
            if not valid:
                raise BadRequest(
                    f"{field} must be non-negative money with at most two decimals."
                )
            setattr(item, field, value)
    old_parent = parent(item)
    for field, parent_kind in (
        ("project_id", "projects"),
        ("activity_id", "activities"),
        ("parent_task_id", "tasks"),
    ):
        if field not in fields or field not in data:
            continue
        identifier = (
            integer(data[field], field) if data[field] not in (None, "") else None
        )
        if identifier != getattr(item, field):
            if not manager:
                raise Forbidden("Only the owner or creator can change hierarchy.")
            selected = (
                get_item(parent_kind, identifier, user, edit=True)
                if identifier
                else None
            )
            if (
                field == "parent_task_id"
                and selected is not None
                and selected.id == item.id
            ):
                raise BadRequest("A task cannot be its own parent.")
            if (
                field == "parent_task_id"
                and selected is not None
                and selected.parent_task_id is not None
            ):
                raise BadRequest("Subtasks can only be one level deep.")
            setattr(item, field.removesuffix("_id"), selected)
            setattr(item, field, identifier)
    if isinstance(item, Task) and sum(
        value is not None
        for value in (item.project_id, item.activity_id, item.parent_task_id)
    ) > 1:
        raise BadRequest("Choose only one parent work item.")
    if not creating and old_parent is not parent(item) and descendants(item):
        raise Conflict("Move child items first before changing hierarchy.")
    status_changed = creating or before["status"] != item.status.value
    if status_changed and item.status in TERMINAL:
        outstanding = [row for row in descendants(item) if row.status not in TERMINAL]
        if outstanding:
            counts = completion_counts(outstanding)
            raise Conflict(
                "Complete or cancel child work before closing this item: "
                f"{counts['activities']['outstanding']} activities and "
                f"{counts['tasks']['outstanding']} tasks and subtasks remain outstanding."
            )
    if item.status not in TERMINAL:
        ancestor = parent(item)
        while ancestor is not None:
            if ancestor.status in TERMINAL:
                raise Conflict(
                    "Reopen the parent work item before opening child work."
                )
            ancestor = parent(ancestor)
    if isinstance(item, Task) and "assigned_user_ids" in data:
        values = data["assigned_user_ids"]
        if not isinstance(values, list) or len(values) > 100:
            raise BadRequest("assigned_user_ids must be a list of at most 100 users.")
        assignees = {}
        for value in values:
            assignee = active_user(value)
            assignees[assignee.id] = assignee
        if any(not can_access(root(item), assignee) for assignee in assignees.values()):
            raise BadRequest("Assignees need viewer or editor access first.")
        item.assigned_users = list(assignees.values())
    if not creating:
        # Collection-only edits must also participate in the task's version check.
        item.updated_at = utcnow()
    db.session.add(item)
    db.session.flush()
    audit(item, user, "create" if creating else "update", before, serialize(item))
    return item


def descendants(item, include_deleted=False):
    def children(cls, **filters):
        query = cls.query.filter_by(**filters)
        if not include_deleted:
            query = query.filter(cls.deleted_at.is_(None))
        return query.all()

    if isinstance(item, Project):
        activities = children(Activity, project_id=item.id)
        tasks = children(Task, project_id=item.id, parent_task_id=None)
        for activity in activities:
            tasks.extend(children(Task, activity_id=activity.id, parent_task_id=None))
        subtasks = [
            subtask
            for task in tasks
            for subtask in children(Task, parent_task_id=task.id)
        ]
        return activities + tasks + subtasks
    if isinstance(item, Activity):
        tasks = children(Task, activity_id=item.id, parent_task_id=None)
        return tasks + [
            subtask
            for task in tasks
            for subtask in children(Task, parent_task_id=task.id)
        ]
    if isinstance(item, Task):
        return children(Task, parent_task_id=item.id)
    return []


def delete_item(item, data, user):
    if not can_manage(item, user):
        raise Forbidden("Only the owner or creator can delete work.")
    check_version(item, data)
    if descendants(item):
        raise Conflict("Delete or move child items first.")
    before = serialize(item)
    item.deleted_at = utcnow()
    db.session.flush()
    audit(item, user, "delete", before, serialize(item))


def visible_items(user):
    return {
        kind: visible_query(kind, user).order_by(cls.id.desc()).all()
        for kind, cls in MODELS.items()
    }


def work_hierarchy(items, user, include_children=True, include_ancestors=True):
    """Build one visibility-safe forest, preserving work and subtask parents."""
    selected = {(kind_of(item), item.id) for item in items}
    rows = {}
    for kind, cls in MODELS.items():
        ids = [identifier for item_kind, identifier in selected if item_kind == kind]
        if ids:
            rows.update(
                {
                    (kind, row.id): row
                    for row in visible_query(kind, user).filter(cls.id.in_(ids)).all()
                }
            )
    if include_children:
        project_ids = [identifier for kind, identifier in rows if kind == "projects"]
        if project_ids:
            rows.update(
                {
                    ("activities", row.id): row
                    for row in visible_query("activities", user)
                    .filter(Activity.project_id.in_(project_ids))
                    .all()
                }
            )
        activity_ids = [identifier for kind, identifier in rows if kind == "activities"]
        if project_ids or activity_ids:
            rows.update(
                {
                    ("tasks", row.id): row
                    for row in visible_query("tasks", user)
                    .filter(
                        or_(
                            Task.project_id.in_(project_ids),
                            Task.activity_id.in_(activity_ids),
                        )
                    )
                    .all()
                }
            )
        task_ids = [identifier for kind, identifier in rows if kind == "tasks"]
        if task_ids:
            rows.update(
                {
                    ("tasks", row.id): row
                    for row in visible_query("tasks", user)
                    .filter(Task.parent_task_id.in_(task_ids))
                    .all()
                }
            )
    content_keys = set(rows)
    if include_ancestors:
        parent_task_ids = {
            row.parent_task_id
            for row in rows.values()
            if isinstance(row, Task) and row.parent_task_id
        }
        if parent_task_ids:
            rows.update(
                {
                    ("tasks", row.id): row
                    for row in visible_query("tasks", user)
                    .filter(Task.id.in_(parent_task_ids))
                    .all()
                }
            )
        activity_ids = {
            row.activity_id
            for row in rows.values()
            if isinstance(row, Task) and row.activity_id
        }
        if activity_ids:
            rows.update(
                {
                    ("activities", row.id): row
                    for row in visible_query("activities", user)
                    .filter(Activity.id.in_(activity_ids))
                    .all()
                }
            )
        project_ids = {
            row.project_id
            for row in rows.values()
            if isinstance(row, (Activity, Task)) and row.project_id
        }
        if project_ids:
            rows.update(
                {
                    ("projects", row.id): row
                    for row in visible_query("projects", user)
                    .filter(Project.id.in_(project_ids))
                    .all()
                }
            )
    nodes = {
        key: {
            "item": serialize(row),
            "children": [],
            "context_only": key not in content_keys,
            "selected": key in selected,
            "status_actions": status_actions(row, user),
        }
        for key, row in rows.items()
    }
    forest = []
    for key, row in rows.items():
        parent_key = None
        if isinstance(row, Task) and row.activity_id:
            parent_key = ("activities", row.activity_id)
        elif isinstance(row, Task) and row.parent_task_id:
            parent_key = ("tasks", row.parent_task_id)
        elif isinstance(row, (Activity, Task)) and row.project_id:
            parent_key = ("projects", row.project_id)
        if parent_key in nodes:
            nodes[parent_key]["children"].append(nodes[key])
        else:
            forest.append(nodes[key])

    def aggregate(node):
        row = rows[(node["item"]["kind"], node["item"]["id"])]
        work = [] if node["context_only"] else [row]
        for child in node["children"]:
            work.extend(aggregate(child))
        node["activity_count"] = sum(
            child["activity_count"] + int(child["item"]["kind"] == "activities")
            for child in node["children"]
        )
        tasks = [item for item in work if isinstance(item, Task)]
        node["task_count"] = len(tasks)
        node["progress"] = progress(tasks)
        node["completion"] = completion_counts(
            [item for item in work if item is not row]
        )
        node["rollup"] = financials(work)
        node["overdue_count"] = sum(
            bool(
                item.deadline
                and item.deadline < date.today()
                and item.status not in TERMINAL
            )
            for item in work
        )
        node["children"].sort(
            key=lambda child: (
                child["item"]["kind"] != "activities",
                child["item"]["name"].casefold(),
                child["item"]["id"],
            )
        )
        return work

    for node in forest:
        aggregate(node)
    forest.sort(
        key=lambda node: (
            ("projects", "activities", "tasks").index(node["item"]["kind"]),
            node["item"]["name"].casefold(),
            node["item"]["id"],
        )
    )
    return forest


def root_access(cls, user):
    if not user.is_active:
        return db.false()
    if user.is_admin:
        return db.true()
    field = {
        "projects": Share.project_id,
        "activities": Share.activity_id,
        "tasks": Share.task_id,
    }[next(key for key, model in MODELS.items() if model is cls)]
    recipients = [Share.user_id == user.id]
    if user.department:
        recipients.append(Share.department == user.department)
    granted = db.select(Share.id).where(field == cls.id, or_(*recipients)).exists()
    return or_(cls.owner_id == user.id, cls.created_by == user.id, granted)


def visible_query(kind, user):
    projects = db.select(Project.id).where(
        Project.deleted_at.is_(None), root_access(Project, user)
    )
    activities = db.select(Activity.id).where(
        Activity.deleted_at.is_(None),
        or_(
            Activity.project_id.in_(projects),
            db.and_(Activity.project_id.is_(None), root_access(Activity, user)),
        ),
    )
    root_tasks = db.select(Task.id).where(
        Task.deleted_at.is_(None),
        Task.parent_task_id.is_(None),
        or_(
            Task.project_id.in_(projects),
            Task.activity_id.in_(activities),
            db.and_(
                Task.project_id.is_(None),
                Task.activity_id.is_(None),
                root_access(Task, user),
            ),
        ),
    )
    if kind == "projects":
        return Project.query.filter(Project.id.in_(projects))
    if kind == "activities":
        return Activity.query.filter(Activity.id.in_(activities))
    return Task.query.filter(
        Task.deleted_at.is_(None),
        or_(
            Task.project_id.in_(projects),
            Task.activity_id.in_(activities),
            Task.parent_task_id.in_(root_tasks),
            db.and_(
                Task.project_id.is_(None),
                Task.activity_id.is_(None),
                Task.parent_task_id.is_(None),
                root_access(Task, user),
            ),
        ),
    )


def reference_match(kind, value):
    """Match a complete location reference without materializing visible work."""
    match = re.fullmatch(r"([AT]?)([0-9]+(?:\.[0-9]+){0,2})", value.upper())
    if not match:
        return db.false()
    prefix, path = match.groups()
    components = path.split(".")
    if any(len(part.lstrip("0")) > 18 for part in components):
        return db.false()
    numbers = [int(part) for part in components]
    if kind == "projects" and not prefix and len(numbers) == 1:
        return Project.id == numbers[0]
    if kind == "activities":
        if prefix == "A" and len(numbers) == 1:
            return (Activity.id == numbers[0]) & Activity.project_id.is_(None)
        if not prefix and len(numbers) == 2:
            return (Activity.project_id == numbers[0]) & (Activity.id == numbers[1])
    if kind == "tasks":
        if prefix == "T" and len(numbers) == 1:
            return (
                (Task.id == numbers[0]) & Task.project_id.is_(None)
                & Task.activity_id.is_(None)
            )
        if prefix == "A" and len(numbers) == 2:
            return (Task.id == numbers[1]) & Task.activity.has(
                (Activity.id == numbers[0]) & Activity.project_id.is_(None)
            )
        if not prefix and len(numbers) == 3:
            project_id, activity_id, task_id = numbers
            location = (
                (Task.project_id == project_id) & Task.activity_id.is_(None)
                if activity_id == 0
                else Task.activity.has(
                    (Activity.project_id == project_id) & (Activity.id == activity_id)
                )
            )
            return (Task.id == task_id) & location
    return db.false()


def filtered_query(kind, user, filters):
    cls = MODELS[kind]
    query = visible_query(kind, user)
    search = text(filters.get("q", ""), "search", 200)
    if search:
        query = query.filter(or_(
            cls.name.icontains(search, autoescape=True),
            cls.description.icontains(search, autoescape=True),
            reference_match(kind, search),
        ))
    quick = filters.get("quick")
    allowed_quick = {*QUICK_FILTERS, "assigned"} if kind == "tasks" else set(QUICK_FILTERS)
    if quick not in (None, "") and quick not in allowed_quick:
        raise BadRequest("Choose a valid quick filter.")
    if quick == "mine":
        query = query.filter(cls.owner_id == user.id)
    elif quick == "assigned":
        query = query.filter(Task.assigned_users.any(User.id == user.id))
    elif quick == "open":
        query = query.filter(cls.status.notin_(TERMINAL))
    elif quick == "blocked":
        query = query.filter(cls.status == Status.BLOCKED)
    for field in ("status", "priority", "department", "owner_id"):
        value = filters.get(field)
        if value:
            if field == "status" and value not in {status.value for status in Status}:
                raise BadRequest("Invalid status filter.")
            if field == "priority" and value not in {
                priority.value for priority in Priority
            }:
                raise BadRequest("Invalid priority filter.")
            if field == "owner_id":
                value = integer(value, field)
            query = query.filter(getattr(cls, field) == value)
    scope = filters.get("scope")
    if scope not in (None, "", "standalone", "project", "activity", "mine"):
        raise BadRequest("Invalid scope.")
    if scope == "standalone":
        if kind == "projects":
            query = query.filter(db.false())
        else:
            query = query.filter(cls.project_id.is_(None))
            if kind == "tasks":
                query = query.filter(Task.activity_id.is_(None))
    elif scope == "project":
        if kind == "activities":
            query = query.filter(Activity.project_id.is_not(None))
        elif kind == "tasks":
            query = query.filter(
                or_(
                    Task.project_id.is_not(None),
                    Task.activity_id.in_(
                        db.select(Activity.id).where(Activity.project_id.is_not(None))
                    ),
                    Task.parent_task_id.in_(
                        db.select(Task.id).where(
                            or_(
                                Task.project_id.is_not(None),
                                Task.activity_id.in_(
                                    db.select(Activity.id).where(
                                        Activity.project_id.is_not(None)
                                    )
                                ),
                            )
                        )
                    ),
                )
            )
    elif scope == "activity":
        query = (
            query.filter(Task.activity_id.is_not(None))
            if kind == "tasks"
            else query.filter(db.false())
        )
    elif scope == "mine":
        query = query.filter(cls.owner_id == user.id)
    for field in ("project_id", "activity_id"):
        if filters.get(field):
            identifier = integer(filters[field], field)
            if field == "activity_id":
                query = (
                    query.filter(
                        or_(
                            Task.activity_id == identifier,
                            Task.parent_task_id.in_(
                                db.select(Task.id).where(
                                    Task.activity_id == identifier
                                )
                            ),
                        )
                    )
                    if kind == "tasks"
                    else query.filter(db.false())
                )
            elif kind == "projects":
                query = query.filter(Project.id == identifier)
            elif kind == "activities":
                query = query.filter(Activity.project_id == identifier)
            else:
                query = query.filter(
                    or_(
                        Task.project_id == identifier,
                        Task.activity_id.in_(
                            db.select(Activity.id).where(
                                Activity.project_id == identifier
                            )
                        ),
                        Task.parent_task_id.in_(
                            db.select(Task.id).where(
                                or_(
                                    Task.project_id == identifier,
                                    Task.activity_id.in_(
                                        db.select(Activity.id).where(
                                            Activity.project_id == identifier
                                        )
                                    ),
                                )
                            )
                        ),
                    )
                )
    deadline = filters.get("deadline")
    today = date.today()
    if deadline not in (None, "", "overdue", "upcoming"):
        raise BadRequest("Invalid deadline filter.")
    deadline_filters = {deadline}
    if quick in ("overdue", "upcoming"):
        deadline_filters.add(quick)
    for deadline in deadline_filters - {None, ""}:
        query = query.filter(cls.status.notin_(TERMINAL), cls.deadline.is_not(None))
        query = (
            query.filter(cls.deadline < today)
            if deadline == "overdue"
            else query.filter(cls.deadline.between(today, today + timedelta(days=14)))
        )
    return query.order_by(cls.id.desc())


def due_overview(user, limit=5):
    result = {}
    for deadline in ("overdue", "upcoming"):
        rows = []
        count = 0
        for kind, cls in MODELS.items():
            query = filtered_query(kind, user, {"deadline": deadline}).order_by(None)
            count += query.count()
            rows.extend(query.order_by(cls.deadline, cls.id).limit(limit).all())
        rows.sort(key=lambda item: (item.deadline, item.id))
        result[deadline] = [serialize(item) for item in rows[:limit]]
        result[f"{deadline}_count"] = count
    return result


def progress(items):
    complete = sum(item.status == Status.COMPLETED for item in items)
    total = sum(item.status != Status.CANCELLED for item in items)
    return {
        "completed": complete,
        "total": total,
        "percent": round(100 * complete / total, 1) if total else 0,
    }


def completion_counts(items):
    result = {}
    for kind, cls in MODELS.items():
        rows = [item for item in items if isinstance(item, cls)]
        completed = sum(item.status == Status.COMPLETED for item in rows)
        cancelled = sum(item.status == Status.CANCELLED for item in rows)
        total = len(rows) - cancelled
        result[kind] = {
            "completed": completed,
            "cancelled": cancelled,
            "outstanding": total - completed,
            "total": total,
            "percent": round(100 * completed / total, 1) if total else 0,
        }
    return result


def log_target(log):
    for kind, field in (
        ("projects", "project_id"),
        ("activities", "activity_id"),
        ("tasks", "task_id"),
    ):
        if getattr(log, field) is not None:
            return db.session.get(MODELS[kind], getattr(log, field))
    raise NotFound()


def serialize_log(log):
    item = log_target(log)
    summary = "Comment added"
    if log.kind == "audit":
        event = json.loads(log.body)
        action = event["action"]
        before, after = event["before"], event["after"]
        summary = {
            "create": "Work created",
            "update": "Work updated",
            "delete": "Work archived",
            "share": "Sharing updated",
            "unshare": "Sharing revoked",
            "log.create": "Comment added",
            "log.update": "Comment edited",
            "log.delete": "Comment deleted",
        }[action]
        if action == "update" and before["status"] != after["status"]:
            summary = f"Status: {before['status'].title()} -> {after['status'].title()}"
    return {
        "id": log.id,
        "body": log.body,
        "kind": log.kind,
        "created_by": log.created_by,
        "created_at": iso_timestamp(log.created_at),
        "updated_at": iso_timestamp(log.updated_at),
        "project_id": log.project_id,
        "activity_id": log.activity_id,
        "task_id": log.task_id,
        "version": log.version,
        "deleted_at": iso_timestamp(log.deleted_at),
        "summary": summary,
        "target_name": item.name,
        "target_reference": work_reference(item),
        "target_kind": kind_of(item),
        "target_id": item.id,
        "target_deleted": item.deleted_at is not None,
    }


def visible_logs(user, filters):
    query = LogEntry.query.filter(
        LogEntry.deleted_at.is_(None),
        or_(
            *[
                getattr(LogEntry, field).in_(
                    visible_query(kind, user).with_entities(cls.id)
                )
                for kind, cls, field in (
                    ("projects", Project, "project_id"),
                    ("activities", Activity, "activity_id"),
                    ("tasks", Task, "task_id"),
                )
            ]
        ),
    )
    log_kind = filters.get("log_kind")
    if log_kind:
        if log_kind not in ("comment", "audit"):
            raise BadRequest("Invalid log type.")
        query = query.filter(LogEntry.kind == log_kind)
    target_search = text(filters.get("target_search", ""), "work search", 200)
    if target_search:
        matches = []
        for target_kind, cls, field in (
            ("projects", Project, "project_id"),
            ("activities", Activity, "activity_id"),
            ("tasks", Task, "task_id"),
        ):
            matches.append(
                getattr(LogEntry, field).in_(
                    visible_query(target_kind, user)
                    .filter(
                        or_(
                            cls.name.icontains(target_search, autoescape=True),
                            reference_match(target_kind, target_search),
                        )
                    )
                    .with_entities(cls.id)
                )
            )
        query = query.filter(or_(*matches))
    kind = filters.get("target_kind")
    if kind:
        if kind not in MODELS:
            raise BadRequest("Invalid work type.")
        field = getattr(
            LogEntry,
            {"projects": "project_id", "activities": "activity_id", "tasks": "task_id"}[
                kind
            ],
        )
        query = query.filter(field.is_not(None))
        if filters.get("target_id"):
            query = query.filter(field == integer(filters["target_id"], "target_id"))
    elif filters.get("target_id"):
        raise BadRequest("Select a work type with the item ID.")
    if filters.get("author_id"):
        query = query.filter(
            LogEntry.created_by == integer(filters["author_id"], "author_id")
        )
    return query.order_by(LogEntry.created_at.desc(), LogEntry.id.desc())


def logs_for_items(items):
    clauses = []
    for kind, field in (
        ("projects", LogEntry.project_id),
        ("activities", LogEntry.activity_id),
        ("tasks", LogEntry.task_id),
    ):
        ids = [item.id for item in items if kind_of(item) == kind]
        if ids:
            clauses.append(field.in_(ids))
    return LogEntry.query.filter(or_(*clauses) if clauses else db.false()).order_by(
        LogEntry.created_at.desc(), LogEntry.id.desc()
    )


def timeline(item, user, archive=False):
    if archive and not can_manage(item, user):
        raise Forbidden()
    targets = [item] + descendants(item, include_deleted=True)
    # Root managers retain deleted-child history. Other readers see only live work.
    targets = [row for row in targets if can_manage(row, user) or can_access(row, user)]
    query = logs_for_items(targets)
    if not archive:
        query = query.filter(LogEntry.deleted_at.is_(None))
    return query.all()


def deadline_bucket(item, today):
    if not item.deadline or item.status in TERMINAL:
        return None
    if item.deadline < today:
        return "overdue"
    if item.deadline <= today + timedelta(days=14):
        return "upcoming"
    return None


def dashboard_attention(visible, user):
    today = date.today()
    open_items = [
        item for items in visible.values() for item in items
        if item.status not in TERMINAL
    ]
    groups = {
        "overdue": [item for item in open_items if deadline_bucket(item, today) == "overdue"],
        "blocked": [item for item in open_items if item.status == Status.BLOCKED],
        "upcoming": [
            item for item in open_items
            if deadline_bucket(item, today) == "upcoming"
        ],
    }
    return {
        group: {
            "total": len(items),
            "counts": {
                kind: sum(isinstance(item, model) for item in items)
                for kind, model in MODELS.items()
            },
            "items": [
                {**serialize(item), "status_actions": status_actions(item, user)}
                for item in sorted(
                    items,
                    key=lambda item: (
                        item.deadline or date.max, item.name.casefold(),
                        kind_of(item), item.id,
                    ),
                )[:5]
            ],
        }
        for group, items in groups.items()
    }


def report(user):
    visible = visible_items(user)
    all_items = sum(visible.values(), [])
    tasks = visible["tasks"]
    today = date.today()

    def due(upcoming):
        items = [
            serialize(item)
            for item in all_items
            if deadline_bucket(item, today) == ("upcoming" if upcoming else "overdue")
        ]
        return sorted(items, key=lambda item: (item["deadline"], item["id"]))

    summaries = {}
    project_tasks = {}
    activity_tasks = {}
    project_costs = {}
    activity_costs = {}
    for item in all_items:
        top = root(item)
        if isinstance(top, Project):
            project_costs.setdefault(top.id, []).append(item)
        if isinstance(item, Activity):
            activity_costs.setdefault(item.id, []).append(item)
        elif isinstance(item, Task) and item.activity_id:
            activity_costs.setdefault(item.activity_id, []).append(item)
    for task in tasks:
        top = root(task)
        if isinstance(top, Project):
            project_tasks.setdefault(top.id, []).append(task)
        if task.activity_id:
            activity_tasks.setdefault(task.activity_id, []).append(task)
    for kind in ("projects", "activities"):
        grouped = project_tasks if kind == "projects" else activity_tasks
        costs = project_costs if kind == "projects" else activity_costs
        summaries[kind] = [
            {
                **serialize(item),
                "progress": progress(grouped.get(item.id, [])),
                "rollup": financials(costs.get(item.id, [])),
                "completion": completion_counts(
                    [row for row in costs.get(item.id, []) if row is not item]
                ),
            }
            for item in visible[kind]
        ]
    workload = {}
    for task in tasks:
        if task.status in TERMINAL:
            continue
        for user_id in [user.id for user in task.assigned_users] or [task.owner_id]:
            entry = workload.setdefault(
                user_id, {"user_id": user_id, "open_tasks": 0, "overdue": 0}
            )
            entry["open_tasks"] += 1
            entry["overdue"] += int(bool(task.deadline and task.deadline < today))
    budget = sum((item.budget for item in all_items), Decimal("0.00"))
    actuals = sum((item.actuals for item in all_items), Decimal("0.00"))
    logs = [
        serialize_log(log)
        for log in logs_for_items(all_items)
        .filter(LogEntry.deleted_at.is_(None))
        .limit(100)
        .all()
    ]
    return {
        **summaries,
        "progress": progress(tasks),
        "completion": completion_counts(all_items),
        "overdue": due(False),
        "upcoming": due(True),
        "overdue_tasks": [item for item in due(False) if item["kind"] == "tasks"],
        "budget": str(budget),
        "actuals": str(actuals),
        "variance": str(budget - actuals),
        "workload": list(workload.values()),
        "timeline": logs,
    }


def save_share(item, data, user):
    if parent(item) is not None or not can_manage(item, user):
        raise Forbidden("Manage sharing on the root item as its owner or creator.")
    if set(data) - {"user_id", "department", "role"}:
        raise BadRequest("Unknown share fields.")
    role = data.get("role")
    if role not in ("viewer", "editor"):
        raise BadRequest("Share role must be viewer or editor.")
    user_id = data.get("user_id") or None
    department = data.get("department") or None
    if bool(user_id) == bool(department):
        raise BadRequest("Select one user OR one department.")
    if user_id:
        user_id = active_user(user_id).id
    else:
        department = text(department, "department", 100, True)
        if not User.query.filter_by(department=department, is_active=True).first():
            raise BadRequest("Select a department with active users.")
    share = Share.query.filter_by(
        **target(item), user_id=user_id, department=department
    ).first()
    before = None if share is None else {"id": share.id, "role": share.role}
    if share is None:
        share = Share(
            **target(item), user_id=user_id, department=department, created_by=user.id
        )
        db.session.add(share)
    share.role = role
    db.session.flush()
    audit(
        item,
        user,
        "share",
        before,
        {
            "id": share.id,
            "user_id": user_id,
            "department": department,
            "role": role,
        },
    )
    return share


def revoke_share(item, identifier, user):
    if parent(item) is not None or not can_manage(item, user):
        raise Forbidden()
    share = Share.query.filter_by(id=identifier, **target(item)).first()
    if share is None:
        raise NotFound()
    before = {
        "id": share.id,
        "user_id": share.user_id,
        "department": share.department,
        "role": share.role,
    }
    db.session.delete(share)
    audit(item, user, "unshare", before, None)


def save_log(data, user, log=None, delete=False):
    if log is None:
        if set(data) - {"project_id", "activity_id", "task_id", "body"}:
            raise BadRequest("Unknown log fields.")
        chosen = [
            (kind, data.get(field))
            for kind, field in (
                ("projects", "project_id"),
                ("activities", "activity_id"),
                ("tasks", "task_id"),
            )
            if data.get(field)
        ]
        if len(chosen) != 1:
            raise BadRequest("Logs require exactly one target.")
        item = get_item(chosen[0][0], integer(chosen[0][1], "target"), user, edit=True)
        log = LogEntry(**target(item), created_by=user.id, kind="comment")
        before = None
        action = "log.create"
    else:
        if set(data) - {"body", "version"}:
            raise BadRequest("Unknown log fields.")
        item = log_target(log)
        if not can_access(item, user) or log.deleted_at:
            raise NotFound()
        if log.kind != "comment":
            raise Forbidden("Automatic audit entries are immutable.")
        if not can_access(item, user, edit=True) or (
            log.created_by != user.id and not can_manage(item, user)
        ):
            raise Forbidden("Only the author or root owner may change comments.")
        check_version(log, data)
        before = serialize_log(log)
        action = "log.delete" if delete else "log.update"
    if delete:
        log.deleted_at = utcnow()
    else:
        log.body = text(data.get("body"), "body", 10000, True)
        log.updated_at = utcnow()
    db.session.add(log)
    db.session.flush()
    audit(item, user, action, before, serialize_log(log))
    return log
