"""Local EventKit bridge. Calendar content never goes to the Epitech API.

An explicit `calendar connect` opts in. Once enabled, unreadable calendars block
new registrations instead of silently reporting the time as free.
"""
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Paris")


class CalendarError(Exception):
    pass


def config_dir():
    return Path(os.environ.get("EPIPLAN_CALENDAR_CONFIG_DIR", "~/.config/epiplan")).expanduser()


def load_config():
    p = config_dir() / "calendar.json"
    if not p.exists():
        return {"enabled": False}
    try:
        value = json.loads(p.read_text())
        if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
            raise ValueError("Invalid calendar config")
        return value
    except (OSError, ValueError) as e:
        raise CalendarError("Cannot read calendar configuration. Run epiplan calendar connect again.") from e


def enabled():
    return load_config().get("enabled", False)


def _private_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, ensure_ascii=False)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def bridge(operation, **payload):
    if sys.platform != "darwin":
        raise CalendarError("Apple Calendar requires macOS. Epitech-only tools still work here.")
    helper = Path(os.environ.get("EPIPLAN_CALENDAR_HELPER", str(HERE / ".build/Epiplan Calendar.app/Contents/MacOS/epiplan-calendar")))
    if not helper.is_file():
        raise CalendarError("Apple Calendar helper missing. Run bin/build-calendar, then epiplan calendar connect.")
    try:
        r = subprocess.run([str(helper)], input=json.dumps({"operation": operation, **payload}),
                           capture_output=True, text=True, timeout=120 if operation == "connect" else 30)
        answer = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        raise CalendarError("Calendar helper failed or timed out. Check macOS calendar permissions.") from e
    if r.returncode or not answer.get("ok"):
        raise CalendarError(answer.get("error", "Apple Calendar failed."))
    return answer["result"]


def status():
    return {**bridge("status"), **load_config()}


def connect(calendar_id=None, auto_sync=True, calendar_title=None):
    result = bridge("connect", calendar_id=calendar_id, calendar_title=calendar_title)
    config = {"enabled": True, "calendar_id": result["calendar_id"],
              "calendar": result["calendar"], "auto_sync": auto_sync,
              "external_calendar_id": result.get("external_calendar_id", result["calendar_id"]),
              "external_calendar": result.get("external_calendar", result["calendar"])}
    _private_json(config_dir() / "calendar.json", config)
    return {**config, "calendars": result["calendars"]}


def calendars():
    return bridge("calendars")["calendars"]


def parse(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def bounds(start, end):
    try:
        s, e = parse(start), parse(end)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CalendarError("Invalid dates. Use ISO dates in Europe/Paris or include an offset.") from exc
    if s >= e:
        raise CalendarError("The end must be after the start.")
    return s, e


def events(start, end):
    s, e = bounds(start, end)
    if (e - s).days > 3660:
        raise CalendarError("Calendar queries are limited to ten years.")
    out = {}
    while s < e:
        nxt = min(s + timedelta(days=365), e)
        for event in bridge("events", start=s.isoformat(), end=nxt.isoformat())["events"]:
            # Include the occurrence date: EventKit uses one identifier for a recurring series.
            out[(event["id"], event["start"])] = event
        s = nxt
    return sorted(out.values(), key=lambda event: parse(event["start"]))


def marker(event_id, login):
    key = f"{login}\0{int(event_id)}"
    return "epiplan://epitech/" + hashlib.sha256(key.encode()).hexdigest()


def external_marker(title, start, end, calendar_id):
    key = json.dumps([title.strip(), parse(start).isoformat(), parse(end).isoformat(), calendar_id], ensure_ascii=False)
    return "epiplan://external/" + hashlib.sha256(key.encode()).hexdigest()


def conflicts(start, end, *, exclude_marker=None):
    s, e = bounds(start, end)
    return [event for event in events(start, end)
            if event.get("busy", True) and event.get("marker") != exclude_marker
            and parse(event["start"]) < e and parse(event["end"]) > s]


def reminders(reminder_minutes):
    """Validate reminders as minutes before the start. None keeps the event's alarms, [] clears them."""
    if reminder_minutes is None:
        return None
    if (not isinstance(reminder_minutes, list) or len(reminder_minutes) > 5
            or any(isinstance(m, bool) or not isinstance(m, int) or not 0 <= m <= 40320 for m in reminder_minutes)):
        raise CalendarError("Reminders: up to 5 whole numbers of minutes before the start, between 0 and 40320.")
    return sorted(set(reminder_minutes))


def upsert(*, title, start, end, marker, calendar_id=None, location="", notes="", all_day=False, alarms=None):
    bounds(start, end)
    config = load_config()
    if not config.get("enabled"):
        raise CalendarError("Apple Calendar is not connected. Run epiplan calendar connect.")
    target = calendar_id or config["calendar_id"]
    folder = config_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(folder / "calendar-state.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_file = folder / "calendar-state.json"
        try:
            state = json.loads(state_file.read_text()) if state_file.exists() else {}
        except (OSError, ValueError) as exc:
            raise CalendarError("Cannot read calendar sync state; refusing to risk duplicate events.") from exc
        previous = state.get(marker, {})
        payload = dict(title=title, start=parse(start).isoformat(), end=parse(end).isoformat(),
                       marker=marker, calendar_id=target, location=location, notes=notes, all_day=all_day, alarms=alarms)
        if previous:
            payload.update(apple_id=previous["id"], previous_start=previous["start"], previous_end=previous["end"])
        result = bridge("upsert", **payload)
        event = result["event"]
        state[marker] = {"id": event["id"], "start": event["start"], "end": event["end"]}
        try:
            _private_json(state_file, state)
        except OSError as exc:
            raise CalendarError("Calendar event was saved, but sync-state persistence failed. Check it in Calendar before retrying.") from exc
        return result


def tracked(marker):
    """True when this Epitech mirror is tracked in the local sync state (i.e. created by epiplan)."""
    path = config_dir() / "calendar-state.json"
    try:
        return marker in (json.loads(path.read_text()) if path.exists() else {})
    except (OSError, ValueError) as exc:
        raise CalendarError("Cannot read calendar sync state; refusing to touch an event.") from exc


def remove(marker):
    """Remove only the Epitech mirror tracked locally, never a personal event."""
    folder = config_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(folder / "calendar-state.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = folder / "calendar-state.json"
        try:
            state = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError) as exc:
            raise CalendarError("Cannot read calendar sync state; refusing to remove an event.") from exc
        previous = state.get(marker)
        if previous is None:
            return {"removed": False, "note": "No tracked calendar copy."}
        result = bridge("remove", marker=marker, apple_id=previous["id"],
                        previous_start=previous["start"], previous_end=previous["end"])
        state.pop(marker, None)
        try:
            _private_json(path, state)
        except OSError as exc:
            raise CalendarError("Calendar copy was removed, but local sync-state update failed.") from exc
        return result
