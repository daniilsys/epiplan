# my.epitech.eu API notes (reverse engineering)

Reference for the endpoints, auth behaviour and business rules observed on my.epitech.eu. Project overview and usage: [../README.md](../README.md).

Legend: ✅ observed for real · 📄 inferred from the OpenAPI spec or the JS bundle · ❓ not verified. Last updated: 2026-10-26.

## 1. API

- React SPA on `https://my.epitech.eu`, NestJS backend on `https://my.epitech.eu/api` ✅.
- **Public Swagger** ✅: `/api/docs`, OpenAPI spec at `/api/docs-json` (610 routes). Source of truth for the tools.
- Errors use the Nest format `{statusCode, error, message}` ✅.

## 2. Authentication

**Login** ✅: Microsoft Entra ID through MSAL.js (`clientId` `6cace631-d5fa-43cb-9903-f88d5ee17576`, tenant `901cb4ca-b862-4029-9306-e5cd0f6d9f86`, scope `User.Read`, cache in `localStorage`). The login page has an "EPITECH ACCOUNT · OFFICE 365" button that starts the Microsoft flow. The Microsoft JWT is exchanged for internal tokens by `POST /api/auth/token` (Microsoft JWT as `Bearer`). These identifiers are public (in the bundle). The SPA registration is a *SPA* app, so device code flow is very likely unavailable (not tested).

**API calls** ✅: `Authorization: Bearer <internal access_token>`. Cookie alone → 401, no credentials → 401.

**SPA token storage** ✅: the SPA keeps its internal tokens in `localStorage["@account"]` and reuses them without contacting Microsoft. `/auth/token` is only called when that entry is missing or dead.

**Internal tokens, measured**:

| | Format | Lifetime | Notes |
|---|---|---|---|
| `access_token` | JWT, fields `cities, cursus, cursusSchoolYear, exp, firstname, iat, lastname, login, promotion, roles, semester, semesterId, tekYear` | **6 h** ✅ | |
| `refresh_token` | **Opaque**, 128 chars ✅ | ✅ **more than 7.8 h and less than ~67 h** of inactivity (refresh OK after 7.8 h; dead after ~67 h) | **Rotation** ✅: every refresh returns a new refresh_token. |

**Refresh** ✅: `POST /api/auth/refresh` with `{"refresh_token": "..."}` as body, **without** `Authorization` header → 201 `{access_token, refresh_token}`. Dead token → 401 `Invalid or expired refresh token`.

**API tokens** ✅: `GET /auth/api_token` → **403** for a student. Not an option.

### Dynamic login (recommended) ✅

The internal refresh_token dies after a few days of inactivity, so the agent logs in through the **Microsoft session** instead, which is much longer-lived (it contains an `ESTSAUTHPERSISTENT` cookie).

```
python epilogin.py setup     # once: visible browser, log in by hand (MFA ok, tick "Stay signed in")
python epilogin.py refresh   # every time: headless SSO, rewrites tokens.json and session.json
```

How `refresh` works: load `session.json` into headless Chromium, **drop `localStorage["@account"]`** (to force the exchange), open the site, click the Office 365 button, let MSAL do a silent SSO, intercept the response of `POST /api/auth/token`, write the pair to `tokens.json` and the renewed cookies back to `session.json`. Tested end to end ✅ (including the automatic fallback from `epiplan.py` after an invalid refresh_token).

Fallback order in `epiplan.py`: access_token → API refresh (`/auth/refresh`) → `epilogin.py refresh` → stop with an explicit error.

If `refresh` fails ("Microsoft session expired"), run `setup` again on a machine with a screen.

### Portability / deployment

The whole state is **`session.json`** (plus `tokens.json`, which is just a cache that can be regenerated).

1. Run `setup` locally.
2. Copy `session.json` to the server (scp, Docker/K8s secret, ...). It is rewritten after each successful login, so keep it on a **persistent writable volume**.
3. Set `EPIPLAN_SESSION` / `EPIPLAN_TOKENS`, then run `python epilogin.py refresh` (needs `pip install -r requirements.txt && playwright install chromium`, or use the `Dockerfile`: mount `/data`).

Caveats:
- ❓ Lifetime of the Microsoft session: depends on the tenant policy, to be measured (note the start date).
- ❓ A different IP/country may trigger Conditional Access or risk detection and ask for MFA again. A server in France should reduce that risk. On failure `refresh` exits with a clear message; redo `setup` locally.
- Do **not** run the same `session.json` on two machines at once (cookies and token rotation).
- Treat `session.json` like a password.

### Rules for the agent client

- Refresh **on demand**: when the access_token `exp` is close (< 10 min) or on a 401. No polling loop.
- Write the new pair **before** using it; one refresh at a time (file lock).
- If every refresh path fails: stop and notify, do not retry in a loop.

## 3. Data model

```
Cursus / School year
Unit template (module, e.g. G-CPE-101)
  └─ Unit instance (id 2857, code MLH-1)   ← MODULE registration
        startDate, endDate, endRegistrationDate, isRegistered, registrationsCount, specialties
        └─ Activity instance (slots?, mandatory, forceRegistration, registrationDeadlineHours)
              └─ Event (planning occurrence)   ← ACTIVITY registration
                    startDate, endDate, maxCapacity, registrationsCount, slotsCount,
                    activitySlotsEnabled, sequentialSlots, unitInstanceId, unitCode, isExam
                    └─ Slot (appointment slots, sometimes several in parallel)
```

Activity → module link ✅: every event carries `unitInstanceId`, `unitInstanceCode`, `unitCode`, `unitName`, `activityInstanceId`. `isCalendarEvent: true` (or no `activityInstanceId`) = calendar event, no module.

## 4. Endpoints

All under `/api`, with `Bearer`.

**Read**

| Need | Endpoint |
|---|---|
| Profile | `GET /auth/verify` ✅, `GET /students/profile` ✅ |
| **Modules of the year + my state** | `GET /units/instances?schoolYear=2026` ✅ → all modules (paginated, `meta.total`) with `isRegistered`, `startDate`, `endDate`, `endRegistrationDate`, `registrationsCount`, `unitCode`, `code` |
| Module detail | `GET /units/instances/{id}` ✅ (`activityInstances`, `projectInstances`, `specialties`, `accessLevel`) |
| Activity | `GET /units/instances/{id}/activities/{activityId}` ✅ (`mandatory`, `forceRegistration`, `slots`, `registrationDeadlineHours`) |
| Planning | `GET /events?startDate&endDate&cursus&semester` ✅ (+ `unitInstanceId=`, `campus=`) |
| My events | same route with `registered=me` ✅ |
| My registration to an event | `GET /events/{id}/registrations/me` ✅ (`null` if not registered) |
| Seats | `registrationsCount` / `maxCapacity` in the event; `GET /events/{id}/registrations/count` ✅ → `{count}` |
| Slots of an event | `GET /events/{id}/slots` ✅ |
| People registered to an event | `GET /events/{id}/registrations` ✅ → array of `{id, eventId, createdAt, cursusYear, registeredBy, student{firstname, lastname, login, promotion, campus, …}}`. Empty for events with slots: read `slots[].registration.student` instead. Other students' data: desktop app only, never passed to the agent. |
| One event | `GET /events/{id}` ✅ (⚠️ returns the raw event, **without** enriched fields `fullTitle`, `unitCode`, `registrationsCount`...: use `GET /events` for those) |
| Yearly planning | `GET /units/instances/annual-planning?schoolYear` ✅ |

Read pitfalls ✅:
- **`GET /events` window ≤ 45 days** (else 400 `startDate and endDate must be within 45 days`).
- `student` is "required" in the spec but useless in practice.
- `GET /events/{id}/slots` exposes **other students' names**: strip them in the tool before passing data to the LLM.
- Without `cursus`/`semester`, `GET /events` returns events of all cursus and calendars (94 vs 44 over 14 days).

**Write** (no body)

| Action | Endpoint | Status |
|---|---|---|
| Event without slots: register | `POST /events/{id}/registrations` | ✅ **200** `{id, eventId, studentId (string), registeredBy: null, createdAt, updatedAt}` (spec says 201) |
| Event without slots: unregister | `DELETE /events/{id}/registrations` | ✅ **200**, body `null` |
| Module: register / unregister | `POST /units/instances/{id}/register` · `DELETE /units/instances/{id}/unregister` (or `/units/{schoolYear}/{unitCode}/{instanceCode}/register`) | 📄 untested |
| Slot | `POST /events/{id}/slots/{slotId}/registrations` · `DELETE ...` | 📄 untested (404 `Slot not found` if the slot does not exist ✅) |

## 5. Business rules

**Observed for real (✅)**

- **Per-activity registration window**: `registrationDeadlineHours` = *registration closes N hours before start*. Outside the window, `POST` → **403 `Registration period is closed`**. Values seen: 24, 12, 0, `null`, and 8,640 (= 360 days, so registrations closed long ago, the Coding Club case). Closing date = `startDate − N h`.
- Same mechanism on exit: `DELETE` outside the window → **403 `Unregistration period is closed`**. ⚠️ The window check runs **before** the existence check: outside the window you get this 403 even if not registered. Inside the window, unregistering while not registered gives **404 `Registration not found`**.
- Double registration → **400 `Already registered to this event`**.
- Unknown event → **404 `Event not found`**; unknown slot → **404 `Slot not found`**.
- After a registration: `registrations/count` +1, the event shows up in `registered=me`, `registrations/me` returns the registration with your profile. After unregistering, everything is back to the initial state.
- Simple registrations on an event without slots have no observable overlap check (untested).
- Modules: `endRegistrationDate` bounds registration; `startDate`/`endDate` frame the module.
- **Sequential slots** (`sequentialSlots: true`, seen on a defense, 2026-11-06): slots starting at the same time form a *block* (3 parallel 30-min slots there). Only the first block with a free slot is open (the last one with `sequentialSlotsDescending: true`); a slot of a later block → **409 `You must fill the current slot block before registering for a later one`**. Both fields are on `/events` list items and on `/events/{id}`; slot objects carry no block id, group them by `startDate`.
- Activities with slots: `slotsCount` is 0 until staff creates the slots (13 out of 14 in the observed window). Nothing to book before that.

**Inferred from bundle messages (📄, never triggered)**

- You must be registered to the module to register to the activity ("student must be registered to the unit to register to this event").
- An event with slots is registered per slot, not per event ("this activity uses slots, register through its slots").
- One slot / one session per activity; same for exams.
- Past events: registration and unregistration forbidden.
- `forceRegistration` modules/activities: auto-registered, unregistration forbidden.
- "this unit is not available for your cursus" message, modules targeted by specialty.
- Overlap: only a warning in the UI, so the API probably lets it through; **the tool must detect it**.
- Unregistering from a slot is impossible if it already has evaluations.

## 6. Tests performed

**Read** ✅ (with a dedicated session): profile, modules, events, registrations/me, count, slots, module detail, annual-planning, `/auth/api_token`.

Key findings:
- "Forgotten modules" (open for registration but not registered) are found through `/units/instances`, **not** `/events`: upcoming activities only ever belong to modules the student is already registered to.
- `GET /events` needs `cursus` and `semester` (taken from `/auth/verify`). Passing `student=<login>` returns **403 `You cannot access this student`**.
- Slot objects carry `registration.student` (name, login) for taken slots: the tools reduce this to `taken` / `mine`.

**Write**: two reversible tests, on non-mandatory activities without slots.

*Test 1, window closed (deadline 8,640 h)*: `POST` → **403 `Registration period is closed`**, `DELETE` → 403 `Unregistration period is closed`. Nothing written.

*Test 2, window open*:

| # | Call | Result |
|---|---|---|
| 1 | `GET /events/{id}/registrations/me` | 200 `null` |
| 2 | `POST /events/999999999/registrations` | 404 `Event not found` |
| 3 | `POST /events/{id}/slots/999999999/registrations` | 404 `Slot not found` |
| 4 | `DELETE /events/{id}/registrations` (not registered) | 404 `Registration not found` |
| 5 | **`POST /events/{id}/registrations`** | **200** `{id, eventId, studentId, registeredBy: null, createdAt, updatedAt}` |
| 6 | `GET .../registrations/me` | 200, registration + profile |
| 7 | `GET .../registrations/count`; `GET /events?...&registered=me` | count +1; the event is listed |
| 8 | `POST` again | 400 `Already registered to this event` |
| 9 | **`DELETE /events/{id}/registrations`** | **200** `null` |
| 10 | `GET .../registrations/me`; count | `null`; back to the original value |

**Auth** ✅: a pasted internal `refresh_token` survived 7.8 h of inactivity but was dead after ~67 h. `epilogin.py setup` + `refresh` (headless SSO) and the automatic fallback in `client.py` were verified end to end. The SPA reuses `localStorage["@account"]` without contacting Microsoft, so the login script removes that key to force the exchange. The login page must be clicked ("Office 365" button) for the SSO to start.

Deliberately not tested: past events, slot registration (`POST .../slots/{id}/registrations`), module registration (may not be reversible), overlaps, full events.

## 7. Open questions

1. Lifetime of the Microsoft session in `session.json` (and behaviour from another IP).
2. Is the old refresh_token revoked after rotation (replaying the old one)?
3. Remaining refusals: full event, overlap, unregistered module, slots less than 24 h away, slot registration (full cycle on an event with slots once one is open).
4. Module registration / unregistration (never tested in write).
5. Exact lifetime of the internal refresh_token (between 7.8 h and ~67 h of inactivity).
