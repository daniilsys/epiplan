import Foundation
import EventKit

// One JSON request on stdin, one JSON response on stdout. No browser or iCloud password.
struct BridgeError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

@main
struct CalendarBridge {
    static let formatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    static func date(_ value: Any?) throws -> Date {
        guard let s = value as? String else { throw BridgeError("Missing date.") }
        if let d = formatter.date(from: s) { return d }
        let f = ISO8601DateFormatter()
        guard let d = f.date(from: s) else { throw BridgeError("Invalid date; include an ISO timezone.") }
        return d
    }

    static func text(_ r: [String: Any], _ key: String) throws -> String {
        guard let s = r[key] as? String, !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw BridgeError("Missing \(key).")
        }
        return s
    }

    static func interval(_ r: [String: Any]) throws -> (Date, Date) {
        let start = try date(r["start"]), end = try date(r["end"])
        guard start < end, end.timeIntervalSince(start) <= 366 * 86400 else {
            throw BridgeError("Use an increasing interval of at most 366 days.")
        }
        return (start, end)
    }

    static func authorized() -> Bool {
        let status = EKEventStore.authorizationStatus(for: .event)
        if #available(macOS 14.0, *) { return status == .fullAccess }
        return status == .authorized
    }

    static func calendar(_ store: EKEventStore, _ identifier: Any?, writable: Bool = false) throws -> EKCalendar {
        guard let id = identifier as? String, let cal = store.calendar(withIdentifier: id) else {
            throw BridgeError("Calendar unavailable. Reconnect with epiplan calendar connect.")
        }
        if writable && !cal.allowsContentModifications { throw BridgeError("Calendar is read-only.") }
        return cal
    }

    static func calendars(_ store: EKEventStore) -> [[String: Any]] {
        store.calendars(for: .event).map {
            ["id": $0.calendarIdentifier, "title": $0.title, "source": $0.source.title,
             "writable": $0.allowsContentModifications]
        }.sorted { ($0["title"] as! String) < ($1["title"] as! String) }
    }

    static func events(_ store: EKEventStore, start: Date, end: Date, calendars: [EKCalendar]?) -> [EKEvent] {
        store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: calendars))
            .filter { $0.startDate < end && $0.endDate > start }
    }

    static func payload(_ e: EKEvent) -> [String: Any] {
        ["id": e.calendarItemIdentifier, "title": e.title ?? "Occupé",
         "start": formatter.string(from: e.startDate), "end": formatter.string(from: e.endDate),
         "calendar_id": e.calendar.calendarIdentifier, "calendar": e.calendar.title,
         "all_day": e.isAllDay, "busy": e.availability != .free && e.status != .canceled,
         "marker": e.url?.absoluteString ?? "", "source": "apple_calendar"]
    }

    static func handle(_ r: [String: Any]) async throws -> [String: Any] {
        let operation = try text(r, "operation")
        if operation == "status" {
            return ["authorized": authorized(), "authorization_status": EKEventStore.authorizationStatus(for: .event).rawValue]
        }
        let store = EKEventStore()
        if operation == "connect" {
            if !authorized() {
                let granted: Bool
                if #available(macOS 14.0, *) {
                    granted = try await store.requestFullAccessToEvents()
                } else {
                    granted = try await withCheckedThrowingContinuation { continuation in
                        store.requestAccess(to: .event) { ok, error in
                            if let error = error { continuation.resume(throwing: error) }
                            else { continuation.resume(returning: ok) }
                        }
                    }
                }
                guard granted else { throw BridgeError("Calendar access refused. Enable Calendars access in macOS Privacy & Security.") }
                store.reset()
            }
            let target: EKCalendar
            if let id = r["calendar_id"] as? String, !id.isEmpty {
                target = try calendar(store, id, writable: true)
            } else if let title = r["calendar_title"] as? String, !title.isEmpty {
                let matches = store.calendars(for: .event).filter { $0.title == title }
                guard matches.count == 1, let chosen = matches.first, chosen.allowsContentModifications else {
                    throw BridgeError("Choose an existing, unique writable calendar name.")
                }
                target = chosen
            } else if let existing = store.calendars(for: .event).first(where: { $0.title == "Epitech (epiplan)" && $0.allowsContentModifications }) {
                target = existing
            } else {
                let cal = EKCalendar(for: .event, eventStore: store)
                cal.title = "Epitech (epiplan)"
                // Use the default writable account (iCloud, local, Exchange, etc.).
                guard let source = store.defaultCalendarForNewEvents?.source ?? store.sources.first(where: { $0.sourceType == .local || $0.sourceType == .calDAV }) else {
                    throw BridgeError("No calendar account available. Add one in Calendar first.")
                }
                cal.source = source
                try store.saveCalendar(cal, commit: true)
                target = cal
            }
            let external = store.defaultCalendarForNewEvents ?? target
            return ["calendar_id": target.calendarIdentifier, "calendar": target.title,
                    "external_calendar_id": external.calendarIdentifier, "external_calendar": external.title,
                    "calendars": calendars(store)]
        }
        guard authorized() else { throw BridgeError("Calendar access missing. Run epiplan calendar connect in Terminal.") }
        switch operation {
        case "calendars":
            return ["calendars": calendars(store)]
        case "events":
            let (start, end) = try interval(r)
            var selected: [EKCalendar]? = nil
            if let ids = r["calendar_ids"] as? [String] {
                selected = try ids.map { try calendar(store, $0) }
            }
            let found = events(store, start: start, end: end, calendars: selected)
            return ["events": found.sorted { $0.startDate < $1.startDate }.map(payload)]
        case "upsert":
            let (start, end) = try interval(r)
            let cal = try calendar(store, r["calendar_id"], writable: true)
            let marker = try text(r, "marker")
            guard marker.hasPrefix("epiplan://"), let url = URL(string: marker) else { throw BridgeError("Invalid epiplan marker.") }
            var found: EKEvent? = nil
            if let id = r["apple_id"] as? String, !id.isEmpty {
                found = store.calendarItem(withIdentifier: id) as? EKEvent
                if let event = found, event.url?.absoluteString != marker {
                    throw BridgeError("Saved event no longer belongs to epiplan; refusing to overwrite it.")
                }
            }
            var matches = events(store, start: start, end: end, calendars: nil).filter { $0.url?.absoluteString == marker }
            if let oldStart = r["previous_start"], let oldEnd = r["previous_end"] {
                matches += events(store, start: try date(oldStart), end: try date(oldEnd), calendars: nil).filter { $0.url?.absoluteString == marker }
            }
            let unique = Dictionary(matches.map { ($0.calendarItemIdentifier, $0) }, uniquingKeysWith: { first, _ in first })
            guard unique.count <= 1 else { throw BridgeError("Multiple copies of this activity exist. Resolve them in Calendar before syncing.") }
            if found == nil { found = unique.values.first }
            if let existing = found {
                guard existing.calendar.allowsContentModifications, !existing.hasRecurrenceRules else {
                    throw BridgeError("Managed event is read-only or recurring; refusing to change it.")
                }
            }
            let event = found ?? EKEvent(eventStore: store)
            let created = found == nil
            event.title = try text(r, "title")
            event.startDate = start
            event.endDate = end
            event.isAllDay = r["all_day"] as? Bool ?? false
            event.calendar = cal
            event.location = r["location"] as? String ?? ""
            event.notes = r["notes"] as? String ?? ""
            event.url = url
            event.availability = .busy
            try store.save(event, span: .thisEvent, commit: true)
            return ["event": payload(event), "created": created]
        case "remove":
            let marker = try text(r, "marker")
            guard marker.hasPrefix("epiplan://epitech/") else { throw BridgeError("Only a managed Epitech copy can be removed.") }
            var event: EKEvent? = nil
            if let id = r["apple_id"] as? String { event = store.calendarItem(withIdentifier: id) as? EKEvent }
            if event == nil, let s = r["previous_start"], let e = r["previous_end"] {
                let matches = events(store, start: try date(s), end: try date(e), calendars: nil).filter { $0.url?.absoluteString == marker }
                guard matches.count <= 1 else { throw BridgeError("Multiple managed copies found; remove them manually in Calendar.") }
                event = matches.first
            }
            guard let found = event else { return ["removed": false] }
            guard found.url?.absoluteString == marker, found.calendar.allowsContentModifications, !found.hasRecurrenceRules else {
                throw BridgeError("Refusing to remove an unrelated, read-only or recurring calendar event.")
            }
            try store.remove(found, span: .thisEvent, commit: true)
            return ["removed": true]
        default:
            throw BridgeError("Unknown calendar operation.")
        }
    }

    static func main() async {
        do {
            let input = FileHandle.standardInput.readDataToEndOfFile()
            guard let request = try JSONSerialization.jsonObject(with: input) as? [String: Any] else { throw BridgeError("Expected JSON object.") }
            let result = try await handle(request)
            let data = try JSONSerialization.data(withJSONObject: ["ok": true, "result": result], options: [.sortedKeys])
            FileHandle.standardOutput.write(data)
        } catch {
            let data = try! JSONSerialization.data(withJSONObject: ["ok": false, "error": error.localizedDescription])
            FileHandle.standardOutput.write(data)
            exit(1)
        }
    }
}
