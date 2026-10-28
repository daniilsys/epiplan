"""Apple Calendar commands. Previews are the default; --apply explicitly saves events."""
import argparse
import os
import sys
from datetime import datetime, timedelta

import apple_calendar as calendar
import tools
from client import ApiError, AuthError


def selected_calendar(name):
    if not name:
        return None
    matches = [cal for cal in calendar.calendars() if cal["title"] == name]
    if len(matches) != 1:
        raise calendar.CalendarError("Choose a unique calendar name from epiplan calendar calendars.")
    if not matches[0]["writable"]:
        raise calendar.CalendarError("This calendar is read-only.")
    return matches[0]["id"]


def show_result(result):
    # Human output deliberately omits internal Epitech and Apple identifiers.
    if not result.get("ok", True):
        print(result.get("error", "Operation refused."), file=sys.stderr)
        for event in result.get("conflicts", []):
            print(f"  {event['start']} – {event['end']} : {event['title']}", file=sys.stderr)
        return 1
    if result.get("dry_run"):
        print("Preview only. Use --apply to save this event.")
    else:
        print("Saved to Apple Calendar.")
    if result.get("result", {}).get("warning"):
        print(result["result"]["warning"], file=sys.stderr)
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    connect = sub.add_parser("connect", help="Allow calendar access and enable automatic sync")
    connect.add_argument("--calendar", help="Existing writable calendar name (default: Epitech (epiplan))")
    connect.add_argument("--no-auto-sync", action="store_true")
    sub.add_parser("status")
    sub.add_parser("calendars")
    schedule = sub.add_parser("schedule", help="Both Epitech and personal activities")
    schedule.add_argument("--days", type=int, default=14)
    check = sub.add_parser("check")
    check.add_argument("start")
    check.add_argument("end")
    sync = sub.add_parser("sync", help="Copy an Epitech activity by its exact title")
    sync.add_argument("title")
    sync.add_argument("--days", type=int, default=44)
    sync.add_argument("--slot-start", help="Exact slot start time (ISO), if the activity has slots")
    add = sub.add_parser("add", help="Add a non-Epitech event after checking both schedules")
    add.add_argument("title")
    add.add_argument("start")
    add.add_argument("end")
    add.add_argument("--location", default="")
    add.add_argument("--notes", default="")
    add.add_argument("--all-day", action="store_true")
    for parser in (sync, add):
        parser.add_argument("--calendar", help="Destination calendar name")
        parser.add_argument("--apply", action="store_true", help="Save after conflict checks (default: preview)")
        parser.add_argument("--allow-overlap", action="store_true", help="Explicitly allow conflicting times")
        parser.add_argument("--reason", default="Selected by the user in the epiplan calendar CLI")
    a = p.parse_args()
    try:
        if a.command == "status":
            state = calendar.status()
            print(f"Calendar access: {'authorized' if state['authorized'] else 'not authorized'}")
            print(f"Automatic sync: {'enabled' if state.get('enabled') and state.get('auto_sync', True) else 'disabled'}")
            if state.get("calendar"):
                print("Destination:", state["calendar"])
        elif a.command == "connect":
            result = calendar.connect(auto_sync=not a.no_auto_sync, calendar_title=a.calendar)
            print("Connected to Apple Calendar:", result["calendar"])
            print("Personal events:", result["external_calendar"])
            print("Automatic registration sync:", "on" if result["auto_sync"] else "off")
        elif a.command == "calendars":
            for cal in calendar.calendars():
                print(f"{cal['title']} ({cal['source']}) — {'writable' if cal['writable'] else 'read-only'}")
        elif a.command == "schedule":
            if not 1 <= a.days <= 366:
                p.error("--days must be between 1 and 366")
            start = datetime.now(calendar.TZ)
            for event in tools.combined_schedule(start.isoformat(), (start + timedelta(days=a.days)).isoformat())["events"]:
                print(f"{event['start']} – {event['end']} [{event['source']}] {event['title']}")
        elif a.command == "check":
            result = tools.check_availability(a.start, a.end)
            if result["available"]:
                print("Available in both schedules.")
            else:
                for event in result["conflicts"]:
                    print(f"Busy: {event['start']} – {event['end']} [{event['source']}] {event['title']}")
                return 1
        else:
            if a.apply:
                os.environ["EPIPLAN_ENABLE_WRITES"] = "1"
            common = dict(reason=a.reason, calendar_id=selected_calendar(a.calendar),
                          allow_overlap=a.allow_overlap, dry_run=not a.apply)
            if a.command == "add":
                result = tools.create_external_event(a.title, a.start, a.end, location=a.location,
                                                      notes=a.notes, all_day=a.all_day, **common)
            else:
                if not 1 <= a.days <= 366:
                    p.error("--days must be between 1 and 366")
                start = datetime.now(calendar.TZ)
                candidates = [e for e in tools.list_events(start.isoformat(), (start + timedelta(days=a.days)).isoformat())
                              if e["title"] == a.title]
                if len(candidates) != 1:
                    raise calendar.CalendarError("Use an exact, unique activity title in the selected date range, or select it through the planning skill.")
                event = candidates[0]
                slot_id = None
                if a.slot_start:
                    matches = [s for s in tools.get_event(event["id"]).get("slots", [])
                               if calendar.parse(s["start"]) == calendar.parse(a.slot_start)]
                    if len(matches) != 1:
                        raise calendar.CalendarError("Choose an existing, unique slot start time.")
                    slot_id = matches[0]["id"]
                result = tools.sync_calendar_event(event["id"], slot_id=slot_id, **common)
            return show_result(result)
        return 0
    except (calendar.CalendarError, ApiError, AuthError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
