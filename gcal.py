#!/usr/bin/env python3
"""Sync my registered my.epitech.eu activities into Google Calendar.

    gcal.py login                          # once: Google consent in the browser
    gcal.py sync [--days 30] [--calendar primary] [--no-prune]

Only activities you are registered to are sent. Each one gets a stable event id (epiplan<id>), so
running sync again updates it instead of duplicating it. Activities you unregister from are removed
from the calendar on the next sync (only events created by epiplan are ever deleted).

Needs ~/.config/epiplan/google_client_secret.json: an OAuth client of type "Desktop app", with the
Google Calendar API enabled in your Google Cloud project.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import tools
from client import ApiError, AuthError

TZ = ZoneInfo("Europe/Paris")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CLIENT_SECRET = Path(os.environ.get("EPIPLAN_GCAL_CLIENT", "~/.config/epiplan/google_client_secret.json")).expanduser()
TOKEN = Path(os.environ.get("EPIPLAN_GCAL_TOKEN", "~/.config/epiplan/google_token.json")).expanduser()
MARK = "epiplan"  # private extended property: marks the events this tool created


def save_credentials(creds):
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())


def load_credentials():
    if not TOKEN.exists():
        sys.exit("Not logged in to Google Calendar: run `epiplan calendar login` first.")
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        save_credentials(creds)
    return creds


def _rfc3339(s):
    """Google needs full RFC 3339 datetimes with seconds; tools.my_schedule gives e.g. 2026-11-02T09:00+01:00."""
    return datetime.fromisoformat(s).isoformat(timespec="seconds")


def to_gcal(ev):
    """One of my activities (tools.my_schedule item) as a Google Calendar event, same id on every sync."""
    return {
        "id": f"{MARK}{ev['id']}",
        "summary": ev["title"],
        "description": ev["module"]["name"] or "",
        "start": {"dateTime": _rfc3339(ev["start"]), "timeZone": "Europe/Paris"},
        "end": {"dateTime": _rfc3339(ev["end"]), "timeZone": "Europe/Paris"},
        "extendedProperties": {"private": {MARK: "1"}},
    }


def upsert(service, cal, body):
    try:
        service.events().update(calendarId=cal, eventId=body["id"], body=body).execute()
    except HttpError as e:
        if e.resp.status != 404:
            raise
        try:
            service.events().insert(calendarId=cal, body=body).execute()
        except HttpError as e2:
            # Google keeps deleted event ids reserved: a 409 means it was deleted by hand in the calendar.
            if e2.resp.status != 409:
                raise
            print(f"Skipped {body['summary']}: deleted by hand in Google Calendar, not re-created.")


def prune(service, cal, keep, time_min, time_max):
    """Delete epiplan events in the window that are no longer in my schedule."""
    removed, page = 0, None
    while True:
        resp = service.events().list(
            calendarId=cal, timeMin=time_min, timeMax=time_max, pageToken=page,
            privateExtendedProperty=f"{MARK}=1",
        ).execute()
        for item in resp.get("items", []):
            if item["id"] not in keep:
                service.events().delete(calendarId=cal, eventId=item["id"]).execute()
                removed += 1
        page = resp.get("nextPageToken")
        if not page:
            return removed


def cmd_login(a):
    if not CLIENT_SECRET.exists():
        sys.exit(f"Missing {CLIENT_SECRET}: create a 'Desktop app' OAuth client in Google Cloud Console "
                 "(Calendar API enabled) and save its JSON there.")
    creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES).run_local_server(port=0)
    save_credentials(creds)
    print("Google Calendar login saved.")


def cmd_sync(a):
    service = build("calendar", "v3", credentials=load_credentials(), cache_discovery=False)
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end = today + timedelta(days=a.days)
    events = tools.my_schedule(today.date().isoformat(), end.date().isoformat())
    keep = set()
    for ev in events:
        body = to_gcal(ev)
        keep.add(body["id"])
        upsert(service, a.calendar, body)
    removed = 0 if a.no_prune else prune(service, a.calendar, keep, today.isoformat(), end.isoformat())
    print(f"{len(events)} registered activities synced, {removed} removed.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    s = sub.add_parser("sync")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--calendar", default="primary", help="Google calendar id (default: your primary calendar)")
    s.add_argument("--no-prune", action="store_true", help="keep events you are no longer registered to")
    s.set_defaults(fn=cmd_sync)
    a = p.parse_args()
    try:
        a.fn(a)
    except (ApiError, AuthError) as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
