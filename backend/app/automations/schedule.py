"""Schedule trigger -> next run instant, with zoneinfo (no cron dependency).

Occurrences are defined in the trigger's LOCAL wall-clock time, so "every Monday at 08:00
Europe/Paris" stays 08:00 across daylight-saving changes. Edge cases, deterministic:
  - a wall time that doesn't exist (spring forward, e.g. 02:30) resolves with the pre-transition
    offset, i.e. it runs at the equivalent instant (03:30 local);
  - an ambiguous wall time (fall back) runs once, at its first occurrence.
`every: "hours"` repeats every N hours within each local day starting at `at` (default 00:00) —
e.g. every 3 h from 08:00 -> 08, 11, 14, 17, 20, 23, then 08 the next day.
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Paris"
DEFAULT_AT = "08:00"
EVERY = ("day", "week", "month", "hours")
_AT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_HORIZON_DAYS = 400  # longest gap is a monthly schedule; this bounds the search safely


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Fuseau horaire inconnu : {name}") from exc


def _at(value: str | None, default: str) -> time:
    m = _AT.match((value or default).strip())
    if not m:
        raise ValueError(f"Heure invalide « {value} » (format HH:MM attendu)")
    return time(int(m.group(1)), int(m.group(2)))


def schedule_errors(trigger: dict) -> list[str]:
    """Human-readable problems with a schedule trigger (empty list = valid)."""
    errors = []
    every = trigger.get("every")
    if every not in EVERY:
        return [f"Fréquence invalide « {every} » (attendu : {', '.join(EVERY)})"]
    try:
        _zone(trigger.get("timezone"))
    except ValueError as exc:
        errors.append(str(exc))
    try:
        _at(trigger.get("at"), DEFAULT_AT)
    except ValueError as exc:
        errors.append(str(exc))
    if every == "week":
        days = trigger.get("weekdays")
        if not isinstance(days, list) or not days or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days):
            errors.append("Jours de la semaine invalides (liste de 0=lundi à 6=dimanche attendue)")
    if every == "month":
        dom = trigger.get("day_of_month")
        if not isinstance(dom, int) or isinstance(dom, bool) or not 1 <= dom <= 28:
            errors.append("Jour du mois invalide (1 à 28)")
    if every == "hours":
        n = trigger.get("interval_hours")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 24:
            errors.append("Intervalle en heures invalide (1 à 24)")
    return errors


def _times_for(trigger: dict, day: date) -> list[time]:
    every = trigger["every"]
    if every == "hours":
        start = _at(trigger.get("at"), "00:00")
        step = int(trigger["interval_hours"]) * 60
        minutes = range(start.hour * 60 + start.minute, 24 * 60, step)
        return [time(m // 60, m % 60) for m in minutes]
    at = _at(trigger.get("at"), DEFAULT_AT)
    if every == "day":
        return [at]
    if every == "week":
        return [at] if day.weekday() in set(trigger["weekdays"]) else []
    if every == "month":
        return [at] if day.day == int(trigger["day_of_month"]) else []
    return []


def next_run_at(trigger: dict | None, after: datetime) -> datetime | None:
    """First occurrence strictly after `after` (UTC-aware result), or None for non-schedule triggers.
    Raises ValueError for an invalid schedule."""
    if not trigger or trigger.get("type") != "schedule":
        return None
    errors = schedule_errors(trigger)
    if errors:
        raise ValueError("; ".join(errors))
    tz = _zone(trigger.get("timezone"))
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    after_utc = after.astimezone(timezone.utc)
    first_day = after_utc.astimezone(tz).date()
    for offset in range(_HORIZON_DAYS):
        day = first_day + timedelta(days=offset)
        for t in _times_for(trigger, day):
            candidate = datetime.combine(day, t, tzinfo=tz).astimezone(timezone.utc)
            if candidate > after_utc:
                return candidate
    return None
