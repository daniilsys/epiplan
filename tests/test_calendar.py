import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import apple_calendar as calendar
import tools


START = "2040-10-23T14:00:00+02:00"
END = "2040-10-23T15:00:00+02:00"


def activity(event_id=1, slots=False, start=START, end=END):
    return {"id": event_id, "startDate": start, "endDate": end, "fullTitle": "Project review",
            "unitInstanceId": 10, "unitCode": "TEST", "unitName": "Test module",
            "activityInstanceId": 5, "activitySlotsEnabled": slots, "registrationsCount": 0,
            "maxCapacity": 12, "registrationDeadlineHours": 0, "isExam": False}


def personal(start=START, end=END, **extra):
    return {"id": "personal", "title": "Dentist", "start": start, "end": end, "marker": "",
            "source": "apple_calendar", "busy": True, "calendar_id": "main", "calendar": "Personal", **extra}


class School:
    profile = {"cursus": "test", "semester": 1, "cursusSchoolYear": 2040}
    login = "student@example.test"

    def __init__(self):
        self.bookings = []
        self.slots = []
        self.writes = []
        self.is_registered = False

    def call(self, method, path, params=None, **kw):
        if method in ("POST", "DELETE"):
            self.writes.append((method, path))
            return {"accepted": True}
        if path == "/units/instances":
            return {"data": [{"id": 10, "isRegistered": True}]}
        if path.endswith("/registrations/me"):
            return {"registered": True} if self.is_registered else None
        if path.endswith("/slots"):
            return self.slots
        if path == "/events":
            return copy.deepcopy(self.bookings)
        raise AssertionError((method, path, params))


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.school = School()
        tools._writes_done = 0
        self.event = activity()
        self.personal = []
        self.config = {"enabled": True, "auto_sync": True, "calendar_id": "main", "calendar": "Personal"}
        patches = [
            patch.object(tools, "_client", self.school),
            patch.object(tools, "AUDIT", Path(self.temp.name) / "audit.jsonl"),
            patch.object(tools, "_find_event", side_effect=lambda _: self.event),
            patch.object(calendar, "load_config", side_effect=lambda: self.config.copy()),
            patch.object(calendar, "events", side_effect=lambda *a: self.personal.copy()),
            patch.object(calendar, "calendars", return_value=[{"id": "main", "title": "Personal", "writable": True}]),
            patch.object(calendar, "upsert", return_value={"event": {"id": "apple"}, "created": True}),
            patch.object(calendar, "remove", return_value={"removed": True}),
            patch.dict(os.environ, {"EPIPLAN_ENABLE_WRITES": "1", "EPIPLAN_MAX_WRITES": "5"}),
        ]
        for mocker in patches:
            mocker.start()
            self.addCleanup(mocker.stop)

    def register(self, **kw):
        return tools.register_event(1, reason="User selected the review", **kw)

    def test_personal_appointment_blocks_registration_before_school_write(self):
        self.personal = [personal()]
        result = self.register(dry_run=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["conflicts"][0]["title"], "Dentist")
        self.assertEqual(self.school.writes, [])
        calendar.upsert.assert_not_called()

    def test_adjacent_appointments_do_not_overlap(self):
        self.personal = [personal(start="2040-10-23T13:00:00+02:00", end=START),
                         personal(start=END, end="2040-10-23T16:00:00+02:00")]
        self.assertTrue(self.register()["ok"])
        self.assertEqual(self.school.writes, [])
        calendar.upsert.assert_not_called()

    def test_successful_registration_mirrors_exactly_once(self):
        result = self.register(dry_run=False)
        self.assertTrue(result["result"]["calendar_synced"])
        self.assertEqual(self.school.writes, [("POST", "/events/1/registrations")])
        calendar.upsert.assert_called_once()
        self.assertEqual(tools._writes_done, 1)

    def test_preview_neither_registers_nor_creates_calendar_event(self):
        result = self.register()
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["would"]["sync_to_apple_calendar"])
        self.assertEqual(self.school.writes, [])
        calendar.upsert.assert_not_called()

    def test_calendar_copy_does_not_use_write_budget(self):
        with patch.dict(os.environ, {"EPIPLAN_MAX_WRITES": "1"}):
            self.assertTrue(self.register(dry_run=False)["ok"])
        self.assertEqual(len(self.school.writes), 1)
        calendar.upsert.assert_called_once()
        self.assertEqual(tools._writes_done, 1)

    def test_calendar_failure_after_school_success_reports_partial_success(self):
        calendar.upsert.side_effect = calendar.CalendarError("permission revoked")
        result = self.register(dry_run=False)
        self.assertTrue(result["ok"])
        self.assertTrue(result["result"]["epitech_registered"])
        self.assertFalse(result["result"]["calendar_synced"])
        self.assertIn("sync_calendar_event only", result["result"]["warning"])
        self.assertEqual(len(self.school.writes), 1)

    def test_local_state_permission_failure_does_not_hide_school_success(self):
        calendar.upsert.side_effect = PermissionError("local state is not writable")
        result = self.register(dry_run=False)
        self.assertTrue(result["result"]["epitech_registered"])
        self.assertFalse(result["result"]["calendar_synced"])
        self.assertEqual(len(self.school.writes), 1)

    def test_calendar_read_failure_blocks_even_an_overlap_override(self):
        calendar.events.side_effect = calendar.CalendarError("access denied")
        self.assertFalse(self.register(allow_overlap=True, dry_run=False)["ok"])
        self.assertEqual(self.school.writes, [])

    def test_readonly_destination_blocks_before_school_registration(self):
        calendar.calendars.return_value[0]["writable"] = False
        self.assertFalse(self.register(dry_run=False)["ok"])
        self.assertEqual(self.school.writes, [])

    def test_epitech_only_installation_keeps_working(self):
        self.config = {"enabled": False}
        result = self.register(dry_run=False)
        self.assertTrue(result["ok"])
        calendar.upsert.assert_not_called()
        calendar.events.assert_not_called()

    def test_free_personal_event_is_not_a_conflict(self):
        self.personal = [personal(busy=False)]
        self.assertTrue(self.register()["ok"])

    def test_booked_slot_not_whole_parent_activity_is_checked(self):
        self.school.bookings = [activity(2, slots=True, start="2040-10-23T09:00:00+02:00", end="2040-10-23T18:00:00+02:00")]
        self.school.slots = [{"id": 5, "startDate": "2040-10-23T10:00:00+02:00", "endDate": "2040-10-23T11:00:00+02:00",
                              "registration": {"student": {"login": self.school.login}}}]
        self.assertTrue(self.register()["ok"])
        result = tools.create_external_event("Appointment", "2040-10-23T10:30:00+02:00", "2040-10-23T11:30:00+02:00", "Requested")
        self.assertFalse(result["ok"])
        self.assertEqual(result["conflicts"][0]["source"], "epitech")

    def test_selected_slot_is_the_time_exported(self):
        self.event = activity(slots=True, start="2040-10-23T09:00:00+02:00", end="2040-10-23T18:00:00+02:00")
        self.school.slots = [{"id": 9, "startDate": START, "endDate": END, "registration": None}]
        result = self.register(slot_id=9, dry_run=False)
        self.assertTrue(result["ok"])
        self.assertEqual(calendar.upsert.call_args.kwargs["start"], "2040-10-23T14:00+02:00")
        self.assertEqual(self.school.writes, [("POST", "/events/1/slots/9/registrations")])

    def test_room_names_are_exported_without_duplicates_or_empty_entries(self):
        self.event["rooms"] = [{"name": " Room A "}, {"name": "Room A"}, {"name": "Room B"},
                               {"name": " "}, {"name": None}, "invalid"]
        result = self.register(dry_run=False)
        self.assertTrue(result["ok"])
        self.assertEqual(calendar.upsert.call_args.kwargs["location"], "Room A, Room B")

    def test_selected_slot_room_takes_precedence_over_parent_room(self):
        self.event = activity(slots=True)
        self.event["rooms"] = [{"name": "Parent room"}]
        self.school.slots = [{"id": 9, "startDate": START, "endDate": END, "registration": None,
                              "rooms": [{"name": "Review room"}]}]
        self.assertTrue(self.register(slot_id=9, dry_run=False)["ok"])
        self.assertEqual(calendar.upsert.call_args.kwargs["location"], "Review room")

    def test_team_booked_slot_is_mine_when_i_am_in_the_team(self):
        self.school.bookings = [activity(2, slots=True, start="2040-10-23T09:00:00+02:00", end="2040-10-23T18:00:00+02:00")]
        self.school.slots = [{"id": 5, "startDate": "2040-10-23T10:00:00+02:00", "endDate": "2040-10-23T11:00:00+02:00",
                              "registration": {"student": None, "projectRegistration": {
                                  "leader": {"login": "other@example.test"},
                                  "members": [{"login": self.school.login, "status": "accepted"}]}}}]
        result = tools.sync_all("Team slot", dry_run=False)
        self.assertEqual(result["result"]["synced"][0]["start"], "2040-10-23T10:00+02:00")
        self.assertEqual(result["result"]["failed"], [])

    def test_missing_registered_slot_fails_closed(self):
        self.school.bookings = [activity(2, slots=True)]
        self.assertFalse(self.register(dry_run=False)["ok"])
        self.assertEqual(self.school.writes, [])

    def test_external_event_checks_both_sources_and_never_writes_to_school(self):
        self.school.bookings = [activity(2)]
        result = tools.create_external_event("Lunch", START, END, "Requested", dry_run=False)
        self.assertFalse(result["ok"])
        calendar.upsert.assert_not_called()
        self.assertEqual(self.school.writes, [])

    def test_reminders_are_passed_to_the_calendar_copy(self):
        result = self.register(dry_run=False, reminder_minutes=[60, 10, 10])
        self.assertTrue(result["ok"])
        self.assertEqual(calendar.upsert.call_args.kwargs["alarms"], [10, 60])

    def test_reminders_are_left_untouched_when_not_requested(self):
        self.register(dry_run=False)
        self.assertIsNone(calendar.upsert.call_args.kwargs["alarms"])

    def test_reminders_are_validated_before_any_write(self):
        for bad in ([-5], [1.5], ["10"], [True], [1, 2, 3, 4, 5, 6], [40321]):
            result = self.register(dry_run=False, reminder_minutes=bad)
            self.assertFalse(result["ok"], bad)
        self.assertEqual(self.school.writes, [])

    def test_manual_sync_and_personal_event_accept_reminders(self):
        tools.sync_calendar_event(1, reason="User asked", dry_run=False, allow_overlap=True, reminder_minutes=[30])
        self.assertEqual(calendar.upsert.call_args.kwargs["alarms"], [30])
        tools.create_external_event("Dentist", START, END, reason="User asked", allow_overlap=True,
                                    dry_run=False, reminder_minutes=[])
        self.assertEqual(calendar.upsert.call_args.kwargs["alarms"], [])

    def test_external_event_preview_and_apply(self):
        preview = tools.create_external_event("Lunch", START, END, "Requested")
        self.assertTrue(preview["dry_run"])
        calendar.upsert.assert_not_called()
        done = tools.create_external_event("Lunch", START, END, "Requested", dry_run=False)
        self.assertTrue(done["ok"])
        calendar.upsert.assert_called_once()
        self.assertEqual(self.school.writes, [])

    def test_external_events_default_to_personal_calendar_not_school_calendar(self):
        self.config["external_calendar_id"] = "private"
        calendar.calendars.return_value.append({"id": "private", "title": "Private", "writable": True})
        result = tools.create_external_event("Lunch", START, END, "Requested", dry_run=False)
        self.assertTrue(result["ok"])
        self.assertEqual(calendar.upsert.call_args.kwargs["calendar_id"], "private")

    def test_mirrors_are_deduplicated_in_combined_schedule(self):
        self.school.bookings = [activity(2)]
        self.personal = [personal(marker=calendar.marker(2, self.school.login)), personal(title="Dentist")]
        merged = tools.combined_schedule(START, END)
        self.assertEqual(len(merged["events"]), 2)
        self.assertEqual(merged["sources_checked"], ["epitech", "apple_calendar"])

    def test_manual_sync_excludes_its_own_registration_and_mirror(self):
        self.school.bookings = [activity()]
        self.personal = [personal(marker=calendar.marker(1, self.school.login))]
        result = tools.sync_calendar_event(1, "Selected by user", dry_run=False)
        self.assertTrue(result["ok"])
        calendar.upsert.assert_called_once()
        self.assertEqual(self.school.writes, [])

    def test_all_day_personal_event_blocks_a_timed_booking(self):
        self.personal = [personal(start="2040-10-23T00:00:00+02:00", end="2040-10-24T00:00:00+02:00", all_day=True)]
        self.assertFalse(self.register()["ok"])

    def test_all_day_requires_midnight_bounds(self):
        result = tools.create_external_event("Holiday", START, END, "Requested", all_day=True)
        self.assertFalse(result["ok"])
        calendar.upsert.assert_not_called()

    def test_unregistration_removes_only_managed_school_mirror(self):
        self.school.is_registered = True
        result = tools.unregister_event(1, "User explicitly cancelled this review", dry_run=False)
        self.assertTrue(result["ok"])
        self.assertEqual(self.school.writes, [("DELETE", "/events/1/registrations")])
        calendar.remove.assert_called_once_with(calendar.marker(1, self.school.login))

    def test_failed_mirror_removal_preserves_successful_unregistration(self):
        self.school.is_registered = True
        calendar.remove.side_effect = calendar.CalendarError("permission revoked")
        result = tools.unregister_event(1, "User cancelled", dry_run=False)
        self.assertTrue(result["result"]["epitech_unregistered"])
        self.assertIn("calendar copy remains", result["result"]["warning"])

    def test_update_requires_a_tracked_copy(self):
        with patch.object(calendar, "tracked", return_value=False):
            result = tools.update_calendar_event(1, "Rename", title="X", dry_run=False)
        self.assertFalse(result["ok"])
        calendar.upsert.assert_not_called()

    def test_update_overrides_fields_and_keeps_school_times(self):
        with patch.object(calendar, "tracked", return_value=True):
            result = tools.update_calendar_event(1, "Rename", title="  Mon projet ", notes="n", reminder_minutes=[10], dry_run=False)
        self.assertTrue(result["ok"])
        sent = calendar.upsert.call_args.kwargs
        self.assertEqual((sent["title"], sent["notes"], sent["alarms"]), ("Mon projet", "n", [10]))
        self.assertEqual(sent["marker"], calendar.marker(1, self.school.login))
        self.assertEqual(self.school.writes, [])

    def test_remove_calendar_event_only_removes_tracked_copy(self):
        with patch.object(calendar, "tracked", return_value=False):
            self.assertFalse(tools.remove_calendar_event(1, "Cleanup", dry_run=False)["ok"])
        calendar.remove.assert_not_called()
        with patch.object(calendar, "tracked", return_value=True):
            self.assertTrue(tools.remove_calendar_event(1, "Cleanup", dry_run=False)["ok"])
        calendar.remove.assert_called_once_with(calendar.marker(1, self.school.login))
        self.assertEqual(self.school.writes, [])

    def test_kill_switch_and_empty_reason_still_protect_writes(self):
        with patch.dict(os.environ, {"EPIPLAN_ENABLE_WRITES": "0"}):
            self.assertFalse(self.register(dry_run=False)["ok"])
        self.assertFalse(tools.create_external_event("Lunch", START, END, "", dry_run=False)["ok"])
        calendar.upsert.assert_not_called()
        self.assertEqual(self.school.writes, [])

    def test_connect_preview_does_not_ask_permission_or_create_calendar(self):
        with patch.object(calendar, "connect") as connect:
            self.assertTrue(tools.connect_apple_calendar("User requested integration")["dry_run"])
            connect.assert_not_called()


class LocalStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"EPIPLAN_CALENDAR_CONFIG_DIR": self.temp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_connect_keeps_private_local_configuration(self):
        with patch.object(calendar, "bridge", return_value={"calendar_id": "main", "calendar": "Epitech", "calendars": []}):
            calendar.connect()
        self.assertTrue(calendar.enabled())
        self.assertEqual((Path(self.temp.name) / "calendar.json").stat().st_mode & 0o777, 0o600)

    def test_corrupted_config_fails_closed(self):
        (Path(self.temp.name) / "calendar.json").write_text("{")
        with self.assertRaises(calendar.CalendarError):
            calendar.enabled()

    def test_repeated_sync_uses_previous_identifier_even_when_time_moves(self):
        (Path(self.temp.name) / "calendar.json").write_text(json.dumps({"enabled": True, "calendar_id": "main"}))
        calls = []
        def bridge(operation, **kw):
            calls.append(kw)
            return {"event": {"id": "persistent", "start": kw["start"], "end": kw["end"]}, "created": len(calls) == 1}
        tag = calendar.marker(1, "student@example.test")
        with patch.object(calendar, "bridge", side_effect=bridge):
            calendar.upsert(title="Review", start=START, end=END, marker=tag)
            calendar.upsert(title="Review moved", start="2040-10-30T14:00:00+01:00", end="2040-10-30T15:00:00+01:00", marker=tag)
        self.assertEqual(calls[1]["apple_id"], "persistent")
        self.assertEqual(calls[1]["previous_start"], START)
        self.assertEqual((Path(self.temp.name) / "calendar-state.json").stat().st_mode & 0o777, 0o600)

    def test_recurring_occurrences_are_not_lost_due_to_shared_identifier(self):
        series = [personal(id="series", start="2040-10-23T14:00:00+02:00", end="2040-10-23T15:00:00+02:00"),
                  personal(id="series", start="2040-10-24T14:00:00+02:00", end="2040-10-24T15:00:00+02:00")]
        with patch.object(calendar, "bridge", return_value={"events": series}):
            self.assertEqual(len(calendar.events("2040-10-23", "2040-10-25")), 2)

    def test_overlap_respects_offsets_across_midnight_and_dst(self):
        event = personal(start="2026-10-25T02:15:00+01:00", end="2026-10-25T02:45:00+01:00")
        with patch.object(calendar, "events", return_value=[event]):
            self.assertFalse(calendar.conflicts("2026-10-25T02:15:00+02:00", "2026-10-25T02:45:00+02:00"))
            self.assertTrue(calendar.conflicts("2026-10-25T01:30:00Z", "2026-10-25T02:00:00Z"))

    def test_external_marker_is_stable_and_account_markers_are_separate(self):
        self.assertEqual(calendar.external_marker("Lunch", START, END, "main"),
                         calendar.external_marker(" Lunch ", START, END, "main"))
        self.assertNotEqual(calendar.marker(1, "one@example.test"), calendar.marker(1, "two@example.test"))


if __name__ == "__main__":
    unittest.main()
