#!/usr/bin/env python3
"""Small CLI on top of the client: check the login and browse / register from a terminal.

Tokens come from ~/.config/epiplan/tokens.json (see README); they are refreshed and, if needed,
renewed through the saved Microsoft session automatically.

    epiplan.py whoami
    epiplan.py list [--days 14] [--open-only]
    epiplan.py register EVENT_ID [--slot SLOT_ID | --any-slot]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from client import ApiError, AuthError, Client
import tools


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_events(c, days):
    now = datetime.now(timezone.utc)
    return c.call("GET", "/events", params={
        "startDate": iso(now),
        "endDate": iso(now + timedelta(days=days)),
        "cursus": c.profile["cursus"],
        "semester": c.profile["semester"],
    })


def is_open(ev):
    """Rough check: not over and not full (does not look at registration windows)."""
    if parse(ev["endDate"]) < datetime.now(timezone.utc):
        return False
    cap = ev.get("maxCapacity")
    return not cap or ev["registrationsCount"] < cap


def my_registration(c, ev):
    if ev.get("mySlotStartDate"):
        return True
    return bool(c.call("GET", f"/events/{ev['id']}/registrations/me"))


def cmd_whoami(c, a):
    print(json.dumps(c.call("GET", "/auth/verify"), indent=2, ensure_ascii=False))


def cmd_list(c, a):
    for ev in sorted(fetch_events(c, a.days), key=lambda e: e["startDate"]):
        if ev.get("isCalendarEvent") or (a.open_only and not is_open(ev)):
            continue
        flag = "slots" if ev.get("activitySlotsEnabled") else "     "
        print(f"{ev['id']:>7} {parse(ev['startDate']).astimezone():%a %d/%m %H:%M} {flag} "
              f"{ev['registrationsCount']}/{ev.get('maxCapacity') or '∞'}  "
              f"{ev.get('unitCode', '')} {ev.get('fullTitle') or ev['title']}")


def cmd_register(c, a):
    tools._client = c
    if a.apply:
        os.environ["EPIPLAN_ENABLE_WRITES"] = "1"
    slot_id = a.slot
    if a.any_slot and slot_id is None:
        available = [s for s in tools.get_event(a.event_id).get("slots", []) if not s["taken"]]
        if not available:
            sys.exit("No free slot. Nothing registered.")
        slot_id = min(available, key=lambda s: s["start"])["id"]
    r = tools.register_event(a.event_id, a.reason, slot_id, a.allow_overlap, dry_run=not a.apply)
    # Keep Epitech and calendar identifiers internal, just like the planning skill.
    if not r["ok"]:
        print(r["error"], file=sys.stderr)
        for event in r.get("conflicts", []):
            print(f"  {event['title']} {event['start']}", file=sys.stderr)
        raise SystemExit(1)
    print("Registration preview. Add --apply to confirm." if r.get("dry_run") else "Registered.")
    if r.get("result", {}).get("warning"):
        print(r["result"]["warning"], file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    s = sub.add_parser("list")
    s.add_argument("--days", type=int, default=14)
    s.add_argument("--open-only", action="store_true")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("register")
    s.add_argument("event_id", type=int)
    s.add_argument("--slot", type=int)
    s.add_argument("--any-slot", action="store_true", help="select the earliest free slot, then check conflicts")
    s.add_argument("--apply", action="store_true", help="register after checking both schedules; default: preview")
    s.add_argument("--allow-overlap", action="store_true")
    s.add_argument("--reason", default="Activity selected by the user in the epiplan CLI")
    s.set_defaults(fn=cmd_register)
    a = p.parse_args()
    try:
        a.fn(Client(), a)
    except (ApiError, AuthError) as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
