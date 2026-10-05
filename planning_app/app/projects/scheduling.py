"""Date geometry for server-rendered, read-only scheduling views."""

from datetime import date, timedelta

from . import services as svc


def schedule(items):
    def hierarchy_key(item):
        path = []
        while item is not None:
            path.append((list(svc.MODELS).index(svc.kind_of(item)), item.id))
            item = svc.parent(item)
        return tuple(reversed(path))

    rows = [svc.serialize(item) for item in sorted(items, key=hierarchy_key)]
    dated = []
    unscheduled = []
    dates = []
    for row in rows:
        values = {
            field: date.fromisoformat(row[field]) if row[field] else None
            for field in ("start_date", "end_date", "deadline")
        }
        if not any(values.values()):
            unscheduled.append(row)
            continue
        start = values["start_date"]
        end = values["end_date"]
        deadline = values["deadline"]
        row["schedule_start"] = (start or end or deadline).isoformat()
        row["schedule_end"] = (end or start or deadline).isoformat()
        row["has_duration"] = start is not None and end is not None
        dated.append(row)
        dates.extend(value for value in values.values() if value is not None)
    if not dates:
        return {"rows": [], "unscheduled": unscheduled, "ticks": [], "today": None}
    first, last = min(dates), max(dates)
    days = (last - first).days + 1

    def position(value):
        return round((value - first).days / days * 100, 4)

    for row in dated:
        start = date.fromisoformat(row["schedule_start"])
        end = date.fromisoformat(row["schedule_end"])
        row["left"] = position(start)
        row["width"] = round(((end - start).days + 1) / days * 100, 4)
        row["deadline_left"] = (
            position(date.fromisoformat(row["deadline"])) if row["deadline"] else None
        )
    offsets = sorted({0, (days - 1) // 4, (days - 1) // 2, (days - 1) * 3 // 4, days - 1})
    ticks = [
        {"label": (first + timedelta(days=offset)).isoformat(),
         "left": position(first + timedelta(days=offset))}
        for offset in offsets
    ]
    today = date.today()
    return {
        "rows": dated, "unscheduled": unscheduled, "ticks": ticks,
        "start": first.isoformat(), "end": last.isoformat(),
        "today": position(today) if first <= today <= last else None,
    }
