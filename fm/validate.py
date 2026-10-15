"""Query-param validation. Every failure raises ApiError with a stable code."""
import re
from datetime import date

from . import towns

MIN_DATE = date(2004, 1, 1)
# Searches accept older full dates (birthdays); they fall back to month/day.
BIRTHDAY_MIN = date(1900, 1, 1)
_STORY_ID = re.compile(r"^[0-9a-f]{16}$")
TRACK_EVENTS = ("share", "copy")


class ApiError(Exception):
    def __init__(self, status, code, message, field=None, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.extra = extra

    def body(self):
        err = {"code": self.code, "message": self.message}
        if self.field:
            err["field"] = self.field
        err.update(self.extra)
        return {"error": err}


def town(value):
    if value is None or not str(value).strip():
        raise ApiError(400, "missing_town", "Pick a Florida town.", field="town")
    name = towns.canonical(value)
    if not name:
        raise ApiError(
            400,
            "invalid_town",
            "That isn't a Florida town we know. Try a city or town name, e.g. Tampa.",
            field="town",
            suggestions=towns.suggestions(value),
        )
    return name


def full_date(value, today, field="date", earliest=MIN_DATE):
    """YYYY-MM-DD between earliest (default MIN_DATE) and today."""
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", value or "")
    if not m:
        raise ApiError(400, "invalid_date", "Use YYYY-MM-DD.", field=field)
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise ApiError(400, "invalid_date", "That date doesn't exist.", field=field)
    if d < earliest or d > today:
        raise ApiError(
            400,
            "date_out_of_range",
            "Date must be between %s and today (%s)." % (earliest.isoformat(), today.isoformat()),
            field=field,
        )
    return d


def search_date(value, today, earliest=MIN_DATE):
    """Returns ("full", date) for YYYY-MM-DD or ("month-day", (m, d)) for MM-DD."""
    if value is None or not str(value).strip():
        raise ApiError(400, "missing_date", "Pick a month/day (MM-DD) or a full date (YYYY-MM-DD).", field="date")
    value = str(value).strip()
    if len(value) == 10:
        return "full", full_date(value, today, earliest=earliest)
    m = re.fullmatch(r"([0-9]{2})-([0-9]{2})", value)
    if not m:
        raise ApiError(400, "invalid_date", "Use MM-DD or YYYY-MM-DD.", field="date")
    mo, dy = int(m.group(1)), int(m.group(2))
    try:
        date(2000, mo, dy)  # leap year, so 02-29 is allowed
    except ValueError:
        raise ApiError(400, "invalid_date", "That month/day doesn't exist.", field="date")
    return "month-day", (mo, dy)


def story_id(value):
    if not isinstance(value, str) or not _STORY_ID.match(value):
        raise ApiError(400, "invalid_story_id", "Story ids are 16 lowercase hex characters.", field="id")
    return value


def track_event(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_body", "Body must be a JSON object.")
    event = payload.get("event")
    if event not in TRACK_EVENTS:
        raise ApiError(400, "invalid_event", "event must be one of: %s." % ", ".join(TRACK_EVENTS), field="event")
    sid = payload.get("storyId")
    if sid is not None:
        story_id(sid)
    return event, sid
