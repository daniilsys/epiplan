# Apple Calendar: school and personal planning

The macOS integration reads calendars already configured in Calendar (iCloud,
local calendars and other accounts visible through EventKit). It uses Apple's
EventKit framework locally. It does not ask for an iCloud password, upload your
personal calendar to Epitech, or require a public calendar URL.

## Connect once

Requires macOS 13+, Python 3.10+ and Xcode command line tools (`xcode-select --install`).
`install.sh` builds the Swift helper on macOS. On other systems the original
Epitech tools still work. To rebuild the helper yourself:

```sh
bash bin/build-calendar
epiplan calendar connect
```

Accept the macOS Calendars permission prompt. Full calendar access is necessary
to read conflicts and save selected activities. If denied, the connection is not
enabled. Use System Settings → Privacy & Security → Calendars to change that
choice. Depending on how you launch epiplan, macOS can attribute permission to
Epiplan Calendar or to the launching application/Terminal.

Connect creates/reuses **Epitech (epiplan)** on the default calendar account.
Epitech copies go there; personal events go to the system's default calendar for
new events. Choose another existing, uniquely named destination with
`epiplan calendar connect --calendar "Study"`, or select a calendar through the
agent. `--no-auto-sync` keeps conflict checks without automatically copying new
registrations. Restart the MCP client after upgrading.

```sh
epiplan calendar status
epiplan calendar calendars
epiplan calendar schedule --days 14
epiplan calendar check '2026-11-03T14:00:00+01:00' '2026-11-03T15:00:00+01:00'
```

Ask in plain language: “Check my school and personal planning”, “Put this review
in Apple Calendar”, or “Add a dentist appointment Tuesday, 14:00 to 15:00”. The
agent shows titles, dates and times, never internal event/slot identifiers.

## Select and synchronize activities

After connection, `register_event` checks registered Epitech activities **and all
readable Apple calendars**. A successful selected registration is automatically
copied into the chosen calendar. For slot activities, only the selected slot's
actual time is copied.
Room names are copied into the Calendar location field, using the selected
slot's room when provided and falling back to the activity's rooms.

Use `sync_all` to copy all your upcoming registered activities (next 60 days by default) in one call;
it is idempotent and reports per-event failures without stopping.

Use `sync_calendar_event` to copy an existing activity without registering to it.
Choose a free or personally booked slot for activities with slots. Copies are
keyed by a hash of the account and school event, so repeating sync updates the
existing copy, including time changes. Own registered events and their managed
copies appear only once in `combined_schedule`.

```sh
# Preview, then save; this does not register at school:
epiplan calendar sync 'Exact activity title'
epiplan calendar sync 'Exact activity title' --apply
# Choose a slot by its time:
epiplan calendar sync 'Exact activity title' --slot-start '2026-11-03T14:00:00+01:00'
```

Unregistering an exact activity through epiplan also removes its tracked calendar
copy when auto-sync is enabled. Personal events are never removed. An unavailable
or manually altered copy is reported instead of deleting unrelated events. If
school registration succeeds but Calendar fails, the result reports partial
success: retry the calendar sync only, never the school registration.

This is **on-demand synchronization**: nothing polls in the background. School
changes outside epiplan (including timetable changes) require another sync. Edits
in Calendar do not register or unregister you at school. Existing school calendar
subscriptions still count as busy: epiplan cannot safely infer that an unmarked
subscription event is a duplicate.

## Reminders

`register_event`, `sync_calendar_event` and `create_external_event` accept
`reminder_minutes`: up to five whole numbers of minutes before the start
(0 to 40320), for example `[60, 10]`. They replace the event's reminders.
Omitting it keeps whatever reminders the event already has, so a plain re-sync
never removes yours; `[]` clears them. Rebuild the helper (`bin/build-calendar`)
and restart the MCP client after upgrading.

## Add personal events

These live in Apple Calendar, never in the school API, and must pass the same
check against both schedules. Repeating the same title/time/destination updates
the same managed personal event.

```sh
# Preview, then save:
epiplan calendar add 'Dentist' '2026-11-03T14:00:00+01:00' '2026-11-03T15:00:00+01:00'
epiplan calendar add 'Dentist' '2026-11-03T14:00:00+01:00' '2026-11-03T15:00:00+01:00' --apply
# Choose a calendar:
epiplan calendar add 'Sport' '2026-11-04T18:00:00+01:00' '2026-11-04T19:00:00+01:00' --calendar 'Personal' --location 'Gym' --apply
# All-day end dates are exclusive, at Paris midnight:
epiplan calendar add 'Holiday' '2026-11-07' '2026-11-08' --all-day --apply
```

The legacy `epiplan register` command now uses the same guarded tools, previews
by default and requires `--apply`. `--any-slot` selects the earliest free slot,
then checks conflicts; it no longer attempts writes until one succeeds.

## Conflict and write semantics

- Back-to-back appointments are allowed; event end times are exclusive.
- Recurring and all-day Apple events are read through EventKit. Events marked
  free or canceled do not block a time.
- Actual reserved Epitech slots are checked, rather than the parent activity's
  multi-hour slot window.
- Dates without an offset use Europe/Paris. Explicit offsets and DST are respected.
  Long read windows are split to respect both APIs' date-range limits.
- Calendar access is opt-in; Epitech-only operation works before connection.
  Once connected, read failures block scheduling rather than assuming “free”.
- Tool writes default to dry runs and require a nonempty audit reason. The kill
  switch applies to every write, but only Epitech writes count against the write
  budget (`EPIPLAN_MAX_WRITES`). Apple Calendar copies and personal events are free.
- `--apply` is explicit CLI authorization. Only use `allow_overlap=True` /
  `--allow-overlap` after the user's choice to accept that conflict. It does not
  ignore a broken connected source.
- Two systems cannot provide an atomic transaction. A calendar may change after
  the check; API errors and partial results must be checked.
- Config, stable event references and audit data stay in private files under
  `~/.config/epiplan/`. Never publish them. A local lock serializes duplicate syncs.
  Cloud propagation to other devices depends on your calendar account's sync.

## Tools

Read: `apple_calendar_status`, `list_apple_calendars`, `combined_schedule`,
`check_availability`.

Write: `connect_apple_calendar`, `sync_calendar_event`, `sync_all`, `update_calendar_event`,
`remove_calendar_event`, `create_external_event`. The update and remove tools only act on copies
epiplan created for Epitech activities, never on personal events, and never change the school registration.
`register_event` and `unregister_event` include automatic calendar follow-up.
Read-only server mode exposes no write tools.

## Validate without touching real calendars

```sh
python -m unittest discover -s tests -v
bash bin/build-calendar
printf '%s' '{"operation":"status"}' | '.build/Epiplan Calendar.app/Contents/MacOS/epiplan-calendar'
```

Tests replace EventKit and the school API with isolated fixtures: conflicts, actual
slot times, previews, budgets, permissions, partial failures, deduplication,
recurring/all-day events and DST offsets. Live access requires your own macOS
authorization. Automated tests never create appointments or school registrations.
