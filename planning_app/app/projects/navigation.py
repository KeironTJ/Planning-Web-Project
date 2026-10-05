"""Validated return locations for the server-rendered work screens."""

from urllib.parse import unquote, urlsplit

from flask import current_app, g, request
from werkzeug.exceptions import BadRequest, MethodNotAllowed, NotFound
from werkzeug.routing import RequestRedirect

from . import projects_bp as bp


RETURN_ENDPOINTS = {
    "projects.dashboard",
    "projects.listing",
    "projects.detail",
    "projects.logs",
    "projects.reports",
    "projects.archive",
    "projects.history",
    "projects.scheduling_timeline",
}


def validate_return_to(value):
    if not value:
        return None
    decoded = unquote(value)
    if "\\" in decoded or any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        raise BadRequest("Invalid work return location.")
    try:
        parts = urlsplit(value)
    except ValueError as error:
        raise BadRequest("Invalid work return location.") from error
    if (
        parts.scheme
        or parts.netloc
        or not value.startswith("/")
        or not decoded.startswith("/")
        or decoded.startswith("//")
    ):
        raise BadRequest("Return location must be a local work screen.")
    path = unquote(parts.path)
    if request.script_root:
        prefix = request.script_root + "/"
        if not path.startswith(prefix):
            raise BadRequest("Return location must be a local work screen.")
        path = path[len(request.script_root) :]
    adapter = current_app.url_map.bind_to_environ(request.environ)
    try:
        endpoint, arguments = adapter.match(path, method="GET")
    except (NotFound, MethodNotAllowed, RequestRedirect) as error:
        raise BadRequest("Return location must be a local work screen.") from error
    if endpoint not in RETURN_ENDPOINTS or (
        endpoint in {"projects.listing", "projects.detail", "projects.history"}
        and arguments["kind"] not in {"projects", "activities", "tasks"}
    ):
        raise BadRequest("Return location must be a local work screen.")
    return value


@bp.app_template_global("work_return_to")
def return_to():
    return g.get("work_return_to")


@bp.app_template_global("work_current_url")
def current_location():
    return request.full_path.removesuffix("?")
