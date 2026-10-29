"""Agent-facing tools for my.epitech.eu.

Plain functions returning small JSON-able dicts. No tokens, no raw API payloads, no other
students' names. Every write goes through `_guarded_write`, which enforces the guardrails
(dry run by default, kill switch, action budget, module allowlist, audit log).

Env:
  EPIPLAN_ENABLE_WRITES=1        writes are refused unless set (kill switch)
  EPIPLAN_MAX_WRITES=5           max real writes per server process
  EPIPLAN_MODULE_ALLOWLIST=A,B   unit codes `register_module` may touch (empty = none)
  EPIPLAN_AUDIT=<path>           JSONL audit log (default ~/.config/epiplan/audit.jsonl)
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from client import ApiError, AuthError, Client
import apple_calendar as calendar
from apple_calendar import CalendarError

TZ = ZoneInfo("Europe/Paris")
MAX_WINDOW_DAYS = 44  # API limit is 45
AUDIT = Path(os.environ.get("EPIPLAN_AUDIT", "~/.config/epiplan/audit.jsonl")).expanduser()

_client = None
_writes_done = 0


def client():
    global _client
    if _client is None:
        _client = Client()
    return _client


# ---------- helpers ----------

def _dt(s):
    """ISO string from the API (UTC) or from the agent ('2026-10-27' or with offset)."""
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def _api(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _local(s):
    return _dt(s).astimezone(TZ).isoformat(timespec="minutes") if s else None


def _closes_at(ev):
    h = ev.get("registrationDeadlineHours")
    return _dt(ev["startDate"]) - timedelta(hours=h or 0)


def _error(e):
    if isinstance(e, ApiError):
        try:
            msg = json.loads(e.body).get("message", e.body)
        except Exception:
            msg = e.body
        return {"ok": False, "error": f"HTTP {e.status}: {msg}"}
    return {"ok": False, "error": str(e)}


def _fetch_events(start, end):
    """Raw enriched events over any range (split in <= 44-day windows), calendars excluded."""
    c, out, cur = client(), {}, _dt(start)
    end = _dt(end)
    while cur < end:
        nxt = min(cur + timedelta(days=MAX_WINDOW_DAYS), end)
        for ev in c.call("GET", "/events", params={
            "startDate": _api(cur), "endDate": _api(nxt),
            "cursus": c.profile["cursus"], "semester": c.profile["semester"],
        }):
            if not ev.get("isCalendarEvent") and ev.get("activityInstanceId"):
                out[ev["id"]] = ev
        cur = nxt
    return list(out.values())


def _my_event_ids(start, end):
    c = client()
    ids, cur, end = set(), _dt(start), _dt(end)
    while cur < end:
        nxt = min(cur + timedelta(days=MAX_WINDOW_DAYS), end)
        for ev in c.call("GET", "/events", params={
            "startDate": _api(cur), "endDate": _api(nxt), "registered": "me",
            "cursus": c.profile["cursus"], "semester": c.profile["semester"],
        }):
            ids.add(ev["id"])
        cur = nxt
    return ids


def _registered_modules():
    return {m["id"] for m in _modules() if m["isRegistered"]}


def _modules(school_year=None):
    c = client()
    return c.call("GET", "/units/instances",
                  params={"schoolYear": school_year or c.profile["cursusSchoolYear"], "limit": 100})["data"]


def _slim_event(ev, mine, now=None):
    now = now or datetime.now(timezone.utc)
    closes = _closes_at(ev)
    cap = ev.get("maxCapacity")
    full = bool(cap) and ev["registrationsCount"] >= cap
    return {
        "id": ev["id"],
        "title": ev.get("fullTitle") or ev.get("activityName") or ev.get("title"),
        "module": {"unit_instance_id": ev["unitInstanceId"], "code": ev.get("unitCode"), "name": ev.get("unitName")},
        "start": _local(ev["startDate"]),
        "end": _local(ev["endDate"]),
        "registered": ev["id"] in mine,
        "registration_closes_at": _local(closes.isoformat()),
        "registration_open": closes > now and _dt(ev["endDate"]) > now and not full,
        "seats": {"registered": ev["registrationsCount"], "max": cap, "full": full},
        "uses_slots": bool(ev.get("activitySlotsEnabled")),
        "slots_count": ev.get("slotsCount", 0),  # total created by staff, taken or not
        "is_exam": bool(ev.get("isExam")),
    }


def _find_event(event_id):
    """Enriched event by id (GET /events/{id} lacks the enriched fields). Widens the window once."""
    raw = client().call("GET", f"/events/{event_id}")
    start = _dt(raw["startDate"])
    for before, after in ((1, 2), (7, 8)):
        for ev in _fetch_events(_api(start - timedelta(days=before)), _api(start + timedelta(days=after))):
            if ev["id"] == event_id:
                return ev
    return None


def _slim_slot(s, me):
    reg = s.get("registration")
    return {"id": s["id"], "start": _local(s["startDate"]), "end": _local(s["endDate"]),
            "taken": bool(reg), "mine": bool(reg and (reg.get("student") or {}).get("login") == me)}


# ---------- read tools ----------

def get_profile() -> dict:
    """Which cohort I am in: cursus, semester, school year. No identity (login, name) is exposed to the agent."""
    p = client().profile
    return {k: p.get(k) for k in ("cursus", "semester", "tekYear", "promotion", "cursusSchoolYear")}


def list_modules(only_unregistered: bool = False, school_year: int | None = None) -> list[dict]:
    """Modules (units) of the school year with my registration state and registration deadline."""
    now = datetime.now(timezone.utc)
    out = []
    for m in _modules(school_year):
        if only_unregistered and m["isRegistered"]:
            continue
        out.append({
            "unit_instance_id": m["id"], "unit_code": m["unitCode"], "name": m["name"],
            "registered": m["isRegistered"],
            "start": _local(m["startDate"]), "end": _local(m["endDate"]),
            "registration_closes_at": _local(m["endRegistrationDate"]),
            "registration_open": _dt(m["endRegistrationDate"]) > now,
        })
    return out


def list_events(start: str, end: str, registered_only: bool = False, only_open: bool = False) -> list[dict]:
    """Planning activities between two dates (ISO; a bare date means Paris midnight; any range).
    `registered_only`: only mine. `only_open`: registration still possible (not full, not closed)."""
    mine = _my_event_ids(start, end)
    now = datetime.now(timezone.utc)
    out = [_slim_event(ev, mine, now) for ev in _fetch_events(start, end)]
    if registered_only:
        out = [e for e in out if e["registered"]]
    if only_open:
        out = [e for e in out if e["registration_open"] and not e["registered"]]
    return sorted(out, key=lambda e: e["start"])


def my_schedule(start: str, end: str) -> list[dict]:
    """Events I am registered to between two dates."""
    return list_events(start, end, registered_only=True)


def get_event(event_id: int) -> dict:
    """One event with my registration state, my module state and its slots (no names)."""
    c = client()
    ev = _find_event(event_id)
    if ev is None:
        # Not in my cursus/semester planning listing: still show what the API exposes about the event.
        raw = c.call("GET", f"/events/{event_id}")
        slots = [_slim_slot(s, c.login) for s in c.call("GET", f"/events/{event_id}/slots")]
        return {"id": event_id, "enriched": False, "start": _local(raw["startDate"]), "end": _local(raw["endDate"]),
                "registered": bool(c.call("GET", f"/events/{event_id}/registrations/me")),
                "slots": slots, "slots_free": sum(1 for s in slots if not s["taken"]),
                "free_slot_times": sorted({s["start"] for s in slots if not s["taken"]}),
                "note": "Event is not in my cursus/semester listing; module and seat info unavailable."}
    mine = {event_id} if c.call("GET", f"/events/{event_id}/registrations/me") else set()
    out = _slim_event(ev, mine)
    out["module"]["registered"] = ev["unitInstanceId"] in _registered_modules()
    out["description"] = (ev.get("activityDescription") or "")[:500]
    if ev.get("activitySlotsEnabled"):
        slots = c.call("GET", f"/events/{event_id}/slots")
        out["slots"] = [_slim_slot(s, c.login) for s in slots]
        out["registered"] = out["registered"] or any(s["mine"] for s in out["slots"])
        out["slots_free"] = sum(1 for s in out["slots"] if not s["taken"])
        out["free_slot_times"] = sorted({s["start"] for s in out["slots"] if not s["taken"]})
    return out


# ---------- write tools ----------

def _audit(entry):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps({"at": datetime.now(TZ).isoformat(timespec="seconds"), **entry}, ensure_ascii=False) + "\n")


def _guarded_write(action, target, reason, dry_run, plan, do, cost=1):
    """Shared tail of every write: dry run, kill switch, budget, audit, API error mapping.
    `cost` counts against EPIPLAN_MAX_WRITES; local Apple Calendar writes pass cost=0, only Epitech writes are budgeted."""
    global _writes_done
    if not reason.strip():
        return {"ok": False, "error": "A non-empty reason is required."}
    if dry_run:
        _audit({"action": action, "target": target, "reason": reason, "dry_run": True})
        return {"ok": True, "dry_run": True, "would": plan}
    if os.environ.get("EPIPLAN_ENABLE_WRITES") != "1":
        return {"ok": False, "error": "Writes are disabled (set EPIPLAN_ENABLE_WRITES=1 on the server)."}
    if _writes_done + cost > int(os.environ.get("EPIPLAN_MAX_WRITES", "5")):
        return {"ok": False, "error": "Write budget for this run exhausted."}
    try:
        result = do()
    except (ApiError, AuthError, CalendarError) as e:
        _audit({"action": action, "target": target, "reason": reason, "ok": False, "error": str(e)[:200]})
        return _error(e)
    _writes_done += cost
    _audit({"action": action, "target": target, "reason": reason, "ok": True})
    answer = {"ok": True, "done": plan}
    if result is not None:
        answer["result"] = result
    return answer


def _epitech_bookings(start, end):
    """Read all my bookings, with the actual booked slot rather than its parent event."""
    c, result, seen = client(), [], set()
    cur, stop = calendar.bounds(start, end)
    while cur < stop:
        nxt = min(cur + timedelta(days=MAX_WINDOW_DAYS), stop)
        raw = c.call("GET", "/events", params={"startDate": _api(cur), "endDate": _api(nxt),
                     "registered": "me", "cursus": c.profile["cursus"], "semester": c.profile["semester"]})
        for ev in raw:
            if ev["id"] in seen:
                continue
            seen.add(ev["id"])
            slots = [None]
            if ev.get("activitySlotsEnabled"):
                slots = [s for s in c.call("GET", f"/events/{ev['id']}/slots") if _slim_slot(s, c.login)["mine"]]
                if not slots:
                    raise CalendarError("A registered Epitech activity has no readable booked slot. Cannot verify availability.")
            for slot in slots:
                s, e = (slot or ev)["startDate"], (slot or ev)["endDate"]
                if _dt(s) >= stop or _dt(e) <= _dt(start):
                    continue
                result.append({"id": ev["id"], "title": ev.get("fullTitle") or ev.get("title") or ev.get("activityName") or "Epitech",
                               "start": _local(s), "end": _local(e), "source": "epitech", "busy": True,
                               "marker": calendar.marker(ev["id"], c.login)})
        cur = nxt
    return sorted(result, key=lambda e: _dt(e["start"]))


def _epitech_conflicts(start, end, exclude_event_id=None):
    s, e = calendar.bounds(start, end)
    return [ev for ev in _epitech_bookings(start, end) if ev["id"] != exclude_event_id
            and _dt(ev["start"]) < e and _dt(ev["end"]) > s]


def _both_conflicts(start, end, exclude_event_id=None, exclude_marker=None):
    if not calendar.enabled():
        raise CalendarError("Connect Apple Calendar first with epiplan calendar connect.")
    school = _epitech_conflicts(start, end, exclude_event_id)
    mirrors = {e["marker"] for e in school}
    return school + [e for e in calendar.conflicts(start, end, exclude_marker=exclude_marker)
                     if e.get("marker") not in mirrors]


def _calendar_event_plan(ev, slot=None):
    chosen = slot or ev
    rooms = chosen.get("rooms") or ev.get("rooms") or []
    location = ", ".join(dict.fromkeys(r["name"].strip() for r in rooms
                                     if isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"].strip()))
    return {"location": location, "title": ev.get("fullTitle") or ev.get("activityName") or ev.get("title") or "Epitech",
            "start": _local(chosen["startDate"]), "end": _local(chosen["endDate"]),
            "marker": calendar.marker(ev["id"], client().login),
            "notes": "Activité Epitech sélectionnée dans epiplan. https://my.epitech.eu"}


def _register_and_sync(c, path, calendar_plan):
    # The school API and EventKit are two independent systems: never retry the school
    # registration if only the calendar copy failed.
    c.call("POST", path)
    if calendar_plan is None:
        return {"epitech_registered": True}
    try:
        result = calendar.upsert(**calendar_plan)
        _audit({"action": "auto_sync_calendar", "target": calendar_plan["marker"], "ok": True})
        return {"epitech_registered": True, "calendar_synced": True, "calendar": result}
    except (CalendarError, OSError) as exc:
        _audit({"action": "auto_sync_calendar", "target": calendar_plan["marker"], "ok": False})
        return {"epitech_registered": True, "calendar_synced": False,
                "warning": f"Epitech registration succeeded; Apple Calendar copy failed: {exc}. Retry sync_calendar_event only, not register_event."}


def _unregister_and_unsync(c, path, tag):
    c.call("DELETE", path)
    if tag is None:
        return {"epitech_unregistered": True}
    try:
        result = calendar.remove(tag)
        _audit({"action": "auto_remove_calendar_copy", "target": tag, "ok": True})
        return {"epitech_unregistered": True, "calendar_removed": result["removed"]}
    except (CalendarError, OSError) as exc:
        _audit({"action": "auto_remove_calendar_copy", "target": tag, "ok": False})
        return {"epitech_unregistered": True, "calendar_removed": False,
                "warning": f"School unregistration succeeded; calendar copy remains: {exc}. Remove only its Epitech copy in Calendar."}


def register_event(event_id: int, reason: str, slot_id: int | None = None, allow_overlap: bool = False,
                   allow_exam: bool = False, dry_run: bool = True, reminder_minutes: list[int] | None = None) -> dict:
    """Register to an event (or to one slot of it). `reason` is mandatory (audit log).
    Checks module registration, window, capacity, overlaps. Exams are refused unless allow_exam=True,
    which the user must have asked for explicitly. dry_run=True by default: nothing is written.
    reminder_minutes: optional Apple Calendar reminders, in minutes before the start (e.g. [60, 10])."""
    try:
        alarms = calendar.reminders(reminder_minutes)
        ev = _find_event(event_id)
        if ev is None:
            return {"ok": False, "error": f"Event {event_id} not found in my planning"}
        c, now = client(), datetime.now(timezone.utc)
        if ev["unitInstanceId"] not in _registered_modules():
            return {"ok": False, "error": f"Not registered to module {ev.get('unitCode')} (register_module first)."}
        if ev.get("isExam") and not allow_exam:
            return {"ok": False, "error": "Exam: refused. Only retry with allow_exam=True if the user explicitly asked for this exam."}
        if _dt(ev["endDate"]) <= now or _closes_at(ev) <= now:
            return {"ok": False, "error": f"Registration closed since {_local(_closes_at(ev).isoformat())}."}
        cap = ev.get("maxCapacity")
        uses_slots = bool(ev.get("activitySlotsEnabled"))
        # With slots, capacity is the free slots: registrationsCount also counts people registered without a slot.
        if not uses_slots and cap and ev["registrationsCount"] >= cap:
            return {"ok": False, "error": "Event is full."}
        if uses_slots and slot_id is None:
            return {"ok": False, "error": "This activity uses slots: call get_event and pass slot_id."}
        if not uses_slots and slot_id is not None:
            return {"ok": False, "error": "This event has no slots."}
        slot = None
        if uses_slots:
            slots = c.call("GET", f"/events/{event_id}/slots")
            if any(_slim_slot(s, c.login)["mine"] for s in slots):
                return {"ok": False, "error": "Already have a slot on this event."}
            slot = next((s for s in slots if s["id"] == slot_id), None)
            if slot is None or slot.get("registration"):
                return {"ok": False, "error": "Slot does not exist or is taken."}
            start, end = _dt(slot["startDate"]), _dt(slot["endDate"])
        else:
            if c.call("GET", f"/events/{event_id}/registrations/me"):
                return {"ok": False, "error": "Already registered."}
            start, end = _dt(ev["startDate"]), _dt(ev["endDate"])
        if not allow_overlap:
            clash = _epitech_conflicts(_api(start), _api(end), exclude_event_id=event_id)
            if calendar.enabled():
                clash += calendar.conflicts(_api(start), _api(end), exclude_marker=calendar.marker(event_id, c.login))
            if clash:
                return {"ok": False, "error": "Overlaps with my Epitech or Apple Calendar events.", "conflicts": clash}
        # Even an explicit overlap override must not hide a broken calendar connection.
        calendar_plan = None
        config = calendar.load_config()
        if config.get("enabled"):
            calendar.events(_api(start), _api(end))
            if config.get("auto_sync", True):
                target = next((cal for cal in calendar.calendars() if cal["id"] == config.get("calendar_id")), None)
                if target is None or not target["writable"]:
                    raise CalendarError("The calendar selected for automatic sync is unavailable or read-only.")
                calendar_plan = {**_calendar_event_plan(ev, slot), "alarms": alarms}
        path = f"/events/{event_id}/slots/{slot_id}/registrations" if uses_slots else f"/events/{event_id}/registrations"
        plan = {"register": ev.get("fullTitle"), "event_id": event_id, "slot_id": slot_id, "start": _local(start.isoformat()),
                "sync_to_apple_calendar": calendar_plan is not None, "reminder_minutes": alarms}
        return _guarded_write("register_event", event_id, reason, dry_run, plan,
                              lambda: _register_and_sync(c, path, calendar_plan))
    except (ApiError, AuthError, CalendarError, ValueError) as e:
        return _error(e)


def unregister_event(event_id: int, reason: str, dry_run: bool = True) -> dict:
    """Cancel my registration to an event (or its slot). Only inside the unregistration window."""
    try:
        ev = _find_event(event_id)
        if ev is None:
            return {"ok": False, "error": f"Event {event_id} not found in my planning"}
        c, now = client(), datetime.now(timezone.utc)
        if _closes_at(ev) <= now:
            return {"ok": False, "error": "Unregistration window closed."}
        path = f"/events/{event_id}/registrations"
        if ev.get("activitySlotsEnabled"):
            mine = next((s for s in c.call("GET", f"/events/{event_id}/slots") if _slim_slot(s, c.login)["mine"]), None)
            if mine is None:
                return {"ok": False, "error": "I have no slot on this event."}
            path = f"/events/{event_id}/slots/{mine['id']}/registrations"
        elif not c.call("GET", f"/events/{event_id}/registrations/me"):
            return {"ok": False, "error": "Not registered."}
        plan = {"unregister": ev.get("fullTitle"), "event_id": event_id}
        config = calendar.load_config()
        tag = calendar.marker(event_id, c.login) if config.get("enabled") and config.get("auto_sync", True) else None
        plan["remove_apple_calendar_copy"] = tag is not None
        return _guarded_write("unregister_event", event_id, reason, dry_run, plan,
                              lambda: _unregister_and_unsync(c, path, tag))
    except (ApiError, AuthError, CalendarError) as e:
        return _error(e)


def register_module(unit_instance_id: int, reason: str, dry_run: bool = True) -> dict:
    """Register to a module. Only unit codes in EPIPLAN_MODULE_ALLOWLIST are allowed (not tested live)."""
    try:
        m = next((m for m in _modules() if m["id"] == unit_instance_id), None)
        if m is None:
            return {"ok": False, "error": "Unknown module."}
        allowed = {x.strip() for x in os.environ.get("EPIPLAN_MODULE_ALLOWLIST", "").split(",") if x.strip()}
        if m["unitCode"] not in allowed:
            return {"ok": False, "error": f"{m['unitCode']} is not in the module allowlist."}
        if m["isRegistered"]:
            return {"ok": False, "error": "Already registered."}
        if _dt(m["endRegistrationDate"]) <= datetime.now(timezone.utc):
            return {"ok": False, "error": "Module registration closed."}
        plan = {"register_module": m["unitCode"], "unit_instance_id": unit_instance_id}
        return _guarded_write("register_module", unit_instance_id, reason, dry_run, plan,
                              lambda: client().call("POST", f"/units/instances/{unit_instance_id}/register"))
    except (ApiError, AuthError) as e:
        return _error(e)


def apple_calendar_status() -> dict:
    """Local macOS Calendar connection state; never opens a permission prompt."""
    return calendar.status()


def list_apple_calendars() -> list[dict]:
    """Calendars available on this Mac, their names and whether they are writable."""
    return calendar.calendars()


def combined_schedule(start: str, end: str) -> dict:
    """Read registered Epitech slots AND Apple calendars; deduplicate managed Epitech copies.
    Refuses to claim availability if either connected source cannot be read."""
    if not calendar.enabled():
        raise CalendarError("Connect Apple Calendar first with epiplan calendar connect.")
    school = _epitech_bookings(start, end)
    mirrors = {e["marker"] for e in school}
    personal = [e for e in calendar.events(start, end) if e.get("marker") not in mirrors]
    return {"events": sorted(school + personal, key=lambda e: _dt(e["start"])),
            "sources_checked": ["epitech", "apple_calendar"]}


def check_availability(start: str, end: str) -> dict:
    """Check a proposed time against BOTH Epitech registrations and Apple calendars."""
    conflicts = _both_conflicts(start, end)
    return {"available": not conflicts, "conflicts": conflicts, "sources_checked": ["epitech", "apple_calendar"]}


def connect_apple_calendar(reason: str, calendar_id: str | None = None,
                           auto_sync: bool = True, dry_run: bool = True) -> dict:
    """Opt in to local Apple Calendar access. Auto-sync chosen registrations by default.
    With no calendar_id, creates/reuses 'Epitech (epiplan)'. macOS permission is required."""
    plan = {"connect_apple_calendar": True, "calendar_id": calendar_id, "auto_sync": auto_sync}
    return _guarded_write("connect_apple_calendar", "local_mac", reason, dry_run, plan,
                          lambda: calendar.connect(calendar_id, auto_sync), cost=0)


def _resolve_slot(event_id, ev, slot_id):
    if ev.get("activitySlotsEnabled"):
        slots = client().call("GET", f"/events/{event_id}/slots")
        mine = [s for s in slots if _slim_slot(s, client().login)["mine"]]
        slot = None
        if slot_id is None and len(mine) == 1:
            slot = mine[0]
        elif slot_id is not None:
            slot = next((s for s in slots if s["id"] == slot_id), None)
        if slot is None:
            raise CalendarError("Choose an existing activity slot before copying it to Calendar.")
        state = _slim_slot(slot, client().login)
        if state["taken"] and not state["mine"]:
            raise CalendarError("This slot is booked by someone else.")
        return slot
    if slot_id is not None:
        raise CalendarError("This activity has no slots.")
    return None


def sync_calendar_event(event_id: int, reason: str, slot_id: int | None = None,
                        calendar_id: str | None = None, allow_overlap: bool = False,
                        dry_run: bool = True, reminder_minutes: list[int] | None = None) -> dict:
    """Copy/update a selected Epitech event in Apple Calendar without registering at school.
    Idempotent per student and school event. Slot activities require a chosen or booked slot.
    reminder_minutes: optional reminders in minutes before the start (e.g. [60, 10]); [] clears them,
    omitted keeps the existing ones."""
    try:
        alarms = calendar.reminders(reminder_minutes)
        ev = _find_event(event_id)
        if ev is None:
            raise CalendarError("Epitech activity not found in your planning.")
        slot = _resolve_slot(event_id, ev, slot_id)
        plan = {**_calendar_event_plan(ev, slot), "alarms": alarms}
        config = calendar.load_config()
        target = calendar_id or config.get("calendar_id")
        cal = next((c for c in calendar.calendars() if c["id"] == target), None)
        if cal is None or not cal["writable"]:
            raise CalendarError("Choose an available writable Apple calendar.")
        conflicts = _both_conflicts(plan["start"], plan["end"], event_id, plan["marker"])
        if conflicts and not allow_overlap:
            return {"ok": False, "error": "Calendar copy overlaps an existing activity.", "conflicts": conflicts}
        return _guarded_write("sync_calendar_event", plan["marker"], reason, dry_run, plan,
                              lambda: calendar.upsert(**plan, calendar_id=calendar_id), cost=0)
    except (ApiError, AuthError, CalendarError, ValueError) as exc:
        return _error(exc)


def sync_all(reason: str, days: int = 60, calendar_id: str | None = None,
             dry_run: bool = True, reminder_minutes: list[int] | None = None) -> dict:
    """Copy ALL my upcoming registered Epitech activities (next `days` days, default 60) to Apple Calendar
    in one call, instead of one sync_calendar_event per event. Idempotent: existing copies are updated.
    Slot activities use the slot I booked. One failing event does not stop the others; see `failed`."""
    try:
        alarms = calendar.reminders(reminder_minutes)
        if days < 1:
            raise CalendarError("days must be at least 1.")
        config = calendar.load_config()
        if not config.get("enabled"):
            raise CalendarError("Connect Apple Calendar first with epiplan calendar connect.")
        target = calendar_id or config.get("calendar_id")
        cal = next((c for c in calendar.calendars() if c["id"] == target), None)
        if cal is None or not cal["writable"]:
            raise CalendarError("Choose an available writable Apple calendar.")
        now = datetime.now(timezone.utc)
        start, end = _api(now), _api(now + timedelta(days=days))
        mine = _my_event_ids(start, end)
        plans, failed = [], []
        for ev in _fetch_events(start, end):
            if ev["id"] not in mine:
                continue
            try:
                slot = None
                if ev.get("activitySlotsEnabled"):
                    slots = [s for s in client().call("GET", f"/events/{ev['id']}/slots") if _slim_slot(s, client().login)["mine"]]
                    if not slots:
                        raise CalendarError("No readable booked slot.")
                    slot = slots[0]
                plans.append({**_calendar_event_plan(ev, slot), "alarms": alarms})
            except (ApiError, AuthError, CalendarError) as exc:
                failed.append({"title": ev.get("fullTitle") or ev.get("title"), "error": str(exc)})
        plans.sort(key=lambda p: p["start"])

        def do():
            synced = []
            for p in plans:
                try:
                    calendar.upsert(**p, calendar_id=calendar_id)
                    synced.append({"title": p["title"], "start": p["start"]})
                except (CalendarError, OSError) as exc:
                    failed.append({"title": p["title"], "error": str(exc)})
            return {"synced": synced, "failed": failed}

        plan = {"sync_to_apple_calendar": [{"title": p["title"], "start": p["start"]} for p in plans],
                "calendar": cal["title"], "failed": failed}
        return _guarded_write("sync_all", f"next_{days}_days", reason, dry_run, plan, do, cost=0)
    except (ApiError, AuthError, CalendarError, ValueError) as exc:
        return _error(exc)


def update_calendar_event(event_id: int, reason: str, title: str | None = None, location: str | None = None,
                          notes: str | None = None, reminder_minutes: list[int] | None = None,
                          slot_id: int | None = None, dry_run: bool = True) -> dict:
    """Edit the Apple Calendar copy of an Epitech activity that epiplan created (title, location, notes,
    reminders). Times always follow the school planning. Omitted fields are reset to the school values
    except reminders, which are kept. Never touches personal events or Epitech itself."""
    try:
        alarms = calendar.reminders(reminder_minutes)
        if title is not None and not title.strip():
            raise CalendarError("The title cannot be empty.")
        ev = _find_event(event_id)
        if ev is None:
            raise CalendarError("Epitech activity not found in your planning.")
        tag = calendar.marker(event_id, client().login)
        if not calendar.tracked(tag):
            raise CalendarError("No Apple Calendar copy of this activity created by epiplan. Use sync_calendar_event first.")
        base = _calendar_event_plan(ev, _resolve_slot(event_id, ev, slot_id))
        plan = {**base, "alarms": alarms}
        for key, value in (("title", title.strip() if title else None), ("location", location), ("notes", notes)):
            if value is not None:
                plan[key] = value
        return _guarded_write("update_calendar_event", tag, reason, dry_run, plan,
                              lambda: calendar.upsert(**plan), cost=0)
    except (ApiError, AuthError, CalendarError, ValueError) as exc:
        return _error(exc)


def remove_calendar_event(event_id: int, reason: str, dry_run: bool = True) -> dict:
    """Delete the Apple Calendar copy of an Epitech activity that epiplan created. Does NOT unregister
    from school (use unregister_event for that) and never deletes personal events."""
    try:
        tag = calendar.marker(event_id, client().login)
        if not calendar.tracked(tag):
            return {"ok": False, "error": "No Apple Calendar copy of this activity created by epiplan."}
        return _guarded_write("remove_calendar_event", tag, reason, dry_run,
                              {"remove_apple_calendar_copy": True, "event_id": event_id},
                              lambda: calendar.remove(tag), cost=0)
    except (ApiError, AuthError, CalendarError) as exc:
        return _error(exc)


def create_external_event(title: str, start: str, end: str, reason: str,
                          calendar_id: str | None = None, location: str = "", notes: str = "",
                          all_day: bool = False, allow_overlap: bool = False, dry_run: bool = True,
                          reminder_minutes: list[int] | None = None) -> dict:
    """Add a personal/non-Epitech event to Apple Calendar after checking BOTH schedules.
    Repeating the same title/time/calendar updates the same event. Never writes to Epitech.
    reminder_minutes: optional reminders in minutes before the start (e.g. [30]); [] clears them."""
    try:
        alarms = calendar.reminders(reminder_minutes)
        s, e = calendar.bounds(start, end)
        if not title.strip():
            raise CalendarError("An event title is required.")
        if all_day and (s.timetz().replace(tzinfo=None) != datetime.min.time()
                        or e.timetz().replace(tzinfo=None) != datetime.min.time()):
            raise CalendarError("All-day events must start and end at midnight; the end date is exclusive.")
        config = calendar.load_config()
        if not config.get("enabled"):
            raise CalendarError("Connect Apple Calendar first with epiplan calendar connect.")
        target = calendar_id or config.get("external_calendar_id") or config["calendar_id"]
        tag = calendar.external_marker(title, start, end, target)
        conflicts = _both_conflicts(start, end, exclude_marker=tag)
        if conflicts and not allow_overlap:
            return {"ok": False, "error": "Personal event overlaps an existing activity.", "conflicts": conflicts}
        # Validate the destination on dry runs as well as real writes.
        cal = next((c for c in calendar.calendars() if c["id"] == target), None)
        if cal is None or not cal["writable"]:
            raise CalendarError("Choose an available writable Apple calendar.")
        plan = {"title": title.strip(), "start": s.isoformat(), "end": e.isoformat(),
                "calendar": cal["title"], "all_day": all_day, "reminder_minutes": alarms}
        return _guarded_write("create_external_event", tag, reason, dry_run, plan,
                              lambda: calendar.upsert(title=title.strip(), start=start, end=end, marker=tag,
                                  calendar_id=target, location=location, notes=notes, all_day=all_day, alarms=alarms),
                              cost=0)
    except (ApiError, AuthError, CalendarError, ValueError) as exc:
        return _error(exc)


READ_TOOLS = [get_profile, list_modules, list_events, my_schedule, get_event,
              apple_calendar_status, list_apple_calendars, combined_schedule, check_availability]
WRITE_TOOLS = [register_event, unregister_event, register_module, connect_apple_calendar,
               sync_calendar_event, sync_all, update_calendar_event, remove_calendar_event, create_external_event]
