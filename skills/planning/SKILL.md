---
name: planning
description: Check Epitech and Apple Calendar planning, synchronize selected activities, and add personal events without overlaps. Use for planning, school registrations, Apple Calendar, availability, or adding non-Epitech appointments.
---

Use the `epitech-planning` MCP tools. If unavailable, explain how to enable the
server and restart the agent. Reply in the user's language (French if unsure).

## Read before proposing or scheduling

1. Read `~/.config/epiplan/rules.md`. Follow the user's preferences. If it still
   contains placeholders, gather read-only information first, then clarify them.
2. Read `get_profile`, `list_modules`, `list_events` (next 14 days, open only), and
   `my_schedule`. Report unregistered modules whose registration window is closing.
3. Read `apple_calendar_status`. If connected, use `combined_schedule` for the same
   range. It includes actual reserved Epitech slots and Apple events, with managed
   school copies deduplicated. Permission/read failure means availability is
   unknown, not free. Explain `epiplan calendar connect` when needed. If not
   connected, clearly state personal-calendar availability was not checked.
4. For slot activities, call `get_event` and use actual free slots. Check proposed
   times with `check_availability` when Apple Calendar is connected. Never treat
   the full parent activity window as the student's reserved slot.

Pass numbers as numbers and booleans as booleans. Never display raw JSON, other
students' names, or internal event/slot/calendar identifiers. Use titles, calendar
names, dates and times in reports, choices and recaps.

## Report

Use compact dates such as `ven. 30/10 14:00` and show:

- Already booked school and personal activities.
- Open activities matching preferences: title, module, date/time, seats, closing.
- Activities needing a choice: optional, busy, far away or filling up.
- Closing windows, missing slots and modules needing attention.
- A brief note for ignored activities (exams, full, ineligible).

For a weekly English goal, inspect the full current week in `my_schedule`,
including past days, before proposing another English activity. Exclude English
support if the user's rules exclude it. Propose missing reviews, kick-offs,
bootstraps and clubs according to the user's rules.

## Selected school activities and automatic Apple Calendar copies

- Present a batch and obtain the user's choice, including the exact slot when
  needed. An “always propose” rule does not authorize registration on its own.
- Preview `register_event` with `dry_run=True`; show conflicts or refusals. Only
  after approval and a passing preview call `dry_run=False` with a short `reason`.
- With Calendar connected and auto-sync enabled, successful registrations are
  automatically copied. Do not also call `sync_calendar_event`, unless repairing
  a reported copy failure. The preview says whether a copy is included.
- To copy a selected activity without registering, preview and confirm
  `sync_calendar_event`. It updates existing copies rather than duplicating them.
- Reminders: when the user asks for one, pass `reminder_minutes` (minutes before the
  start, e.g. `[60, 10]`) to `register_event`, `sync_calendar_event` or
  `create_external_event`. Omit it to keep existing reminders; `[]` clears them.
- If registration succeeds but Calendar fails, report both outcomes. Retry
  `sync_calendar_event` only: never repeat the school registration.
- Never use `allow_overlap=True` without an explicit choice to accept the conflict.
- Never unregister unless the user names the exact activity. With auto-sync,
  unregistration removes its tracked Epitech copy, never a personal event.
- Changes outside epiplan are not background-synced. Explain that another sync is
  needed for changed times or registrations made on the website.
- Module registration stays limited to the allowlist. Propose module registration
  and event registration as two confirmed steps when necessary.

## Personal appointments

- Resolve title, date, start/end, and calendar name (`list_apple_calendars` if the
  user names one). Ask only for missing information needed to place it.
- Preview `create_external_event`; it checks both sources and creates no school
  activity. All-day events use Paris midnight and an exclusive end date.
- After approval, repeat with `dry_run=False` and a reason. Repeated saves of the
  same title/time/calendar update the same managed personal event.
- Personal details belong in the scheduling response, never in a school
  registration reason or a public issue/PR.

## Recap

State what was registered, copied, added or refused, with titles and dates only.
Disclose partial failures and unchecked sources. Claim success only when confirmed
by the tool. On school login failure explain `epiplan login`; on Calendar
permission failure explain `epiplan calendar connect`.
