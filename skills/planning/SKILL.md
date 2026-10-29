---
name: planning
description: Check the student's my.epitech.eu planning and registrations, present a report, and register to activities or modules after asking. Use when the user says "planning", "inscris-moi", "vérifie mon planning", "qu'est-ce qui est ouvert", or invokes /planning.
---

Use the `epitech-planning` MCP tools (prefix `mcp__epitech-planning__`). If they are not available, tell the user to approve/enable the server in `/mcp` and restart the agent, then stop.

Reply in the user's language (French if unsure).

## 1. Gather (read only, no questions yet)

1. Read `~/.config/epiplan/rules.md` (preferences and always/never rules). If it still contains the placeholder examples, say so and ask what the user wants.
2. `get_profile`, then `list_modules` (note unregistered modules whose registration is still open).
3. `list_events(start=today, end=today+14d, only_open=True)` for open activities and `my_schedule` over the same range for what is already booked. Extend the range if asked.
4. For candidates that use slots, call `get_event`: `slots_count` includes taken slots, so use `slots_free` and `free_slot_times` to know what is really bookable.

Always pass numbers as numbers and booleans as booleans.

## 2. Report

A compact report, dates like `ven. 30/10 14:00`:

- **Already registered** (next days): short list.
- **Open and matching the rules**: table with id, title, module, date, seats, closing time.
- **Open, to decide** (no matching rule): same table, one line on why it is uncertain (overlap, optional, far away, filling up).
- **To watch**: closing within 48 h, slots not created yet, unregistered modules with an open window.
- **Ignored**: one line (exams, already full, not eligible).

No raw JSON. Never display other students' names.

## 3. Ask, then act

- Even when a rule says "always register", propose the batch and ask once for the whole batch.
- Anything ambiguous: ask a multiple-choice question (multi-select for "which of these?", one question per slot choice with the free times as options; for a module registration, say it may not be reversible).
- For each chosen item, call the write tool with `dry_run=True` first and show any refusal (closed, full, overlap, module not registered). Only if it passes, call it again with `dry_run=False` and a short `reason`.
- Exams are refused by default. Only pass `allow_exam=True` when the user explicitly names that exam and asks to register; never on your own initiative.
- If an overlap blocks something, tell the user and ask before using `allow_overlap=True`.
- If a module must be registered first, propose `register_module` then the event as two confirmed steps. It only works for unit codes in the server's `EPIPLAN_MODULE_ALLOWLIST`; if refused for that reason, tell the user how to add the code.
- Never unregister anything unless the user explicitly asks for that exact event.

## 4. Wrap up

Final recap: what was registered (title, date), what was refused and why, what to re-check later. The audit log is `~/.config/epiplan/audit.jsonl`.

If a tool returns a login error, tell the user to run `epiplan login` in a terminal, then retry.
