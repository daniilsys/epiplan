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


def _guarded_write(action, target, reason, dry_run, plan, do):
    """Shared tail of every write: dry run, kill switch, budget, audit, API error mapping."""
    global _writes_done
    if dry_run:
        _audit({"action": action, "target": target, "reason": reason, "dry_run": True})
        return {"ok": True, "dry_run": True, "would": plan}
    if os.environ.get("EPIPLAN_ENABLE_WRITES") != "1":
        return {"ok": False, "error": "Writes are disabled (set EPIPLAN_ENABLE_WRITES=1 on the server)."}
    if _writes_done >= int(os.environ.get("EPIPLAN_MAX_WRITES", "5")):
        return {"ok": False, "error": "Write budget for this run exhausted."}
    try:
        result = do()
    except (ApiError, AuthError) as e:
        _audit({"action": action, "target": target, "reason": reason, "ok": False, "error": str(e)[:200]})
        return _error(e)
    _writes_done += 1
    _audit({"action": action, "target": target, "reason": reason, "ok": True})
    return {"ok": True, "done": plan}


def register_event(event_id: int, reason: str, slot_id: int | None = None, allow_overlap: bool = False,
                   allow_exam: bool = False, dry_run: bool = True) -> dict:
    """Register to an event (or to one slot of it). `reason` is mandatory (audit log).
    Checks module registration, window, capacity, overlaps. Exams are refused unless allow_exam=True,
    which the user must have asked for explicitly. dry_run=True by default: nothing is written."""
    try:
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
            mine = _my_event_ids(_api(start - timedelta(days=1)), _api(end + timedelta(days=1)))
            clash = [e for e in _fetch_events(_api(start - timedelta(days=1)), _api(end + timedelta(days=1)))
                     if e["id"] in mine and e["id"] != event_id
                     and _dt(e["startDate"]) < end and _dt(e["endDate"]) > start]
            if clash:
                return {"ok": False, "error": "Overlaps with my events.",
                        "conflicts": [{"id": e["id"], "title": e.get("fullTitle"), "start": _local(e["startDate"])}
                                      for e in clash]}
        path = f"/events/{event_id}/slots/{slot_id}/registrations" if uses_slots else f"/events/{event_id}/registrations"
        plan = {"register": ev.get("fullTitle"), "event_id": event_id, "slot_id": slot_id, "start": _local(start.isoformat())}
        return _guarded_write("register_event", event_id, reason, dry_run, plan, lambda: c.call("POST", path))
    except (ApiError, AuthError) as e:
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
        return _guarded_write("unregister_event", event_id, reason, dry_run, plan, lambda: c.call("DELETE", path))
    except (ApiError, AuthError) as e:
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


READ_TOOLS = [get_profile, list_modules, list_events, my_schedule, get_event]
WRITE_TOOLS = [register_event, unregister_event, register_module]
