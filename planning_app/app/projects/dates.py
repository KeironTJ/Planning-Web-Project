"""UK presentation for work dates; wire values remain ISO."""

from datetime import date, datetime
import re

from werkzeug.exceptions import BadRequest


def uk_date(value):
    if not value:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return f"{value.day:02}/{value.month:02}/{value.year:04}"


def form_date(value, field):
    if not value:
        return ""
    if re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value):
        try:
            return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
        except ValueError:
            pass
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise BadRequest(f"{field.replace('_', ' ').title()} must be a valid date in dd/mm/yyyy format.")
