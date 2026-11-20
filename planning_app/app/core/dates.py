"""Calendar date presentation; stored and wire values remain ISO."""

from datetime import date


def uk_date(value: date | str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return f"{value.day:02}/{value.month:02}/{value.year:04}"
