<div align="center">

# epiplan

**New on macOS:** connect Apple Calendar to copy selected Epitech activities,
check personal appointments before registering, and add non-Epitech events.
Run `epiplan calendar connect` after installation. See [Apple Calendar setup and examples](docs/APPLE_CALENDAR.md).

**Your my.epitech.eu planning, as tools an AI agent can use.**<br>
Check what's open, get a clear report, register to activities and modules — with you in the loop.

<p>
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="mcp" src="https://img.shields.io/badge/MCP-server-6E56CF">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-2EA44F">
  <img alt="status" src="https://img.shields.io/badge/status-unofficial-orange">
</p>

</div>

<br>

<table>
<tr>
<td width="50%" valign="top">

### What it does

- Reads your planning, modules, seats and slots
- Tells you what's **open**, **closing soon**, or **already booked**
- Registers you to events, slots and modules **after you confirm**
- Logs in through your Microsoft account, **no password stored**
- Runs on demand, nothing stays running in the background

</td>
<td width="50%" valign="top">

### What it never does

- Register without a dry run and your approval
- Show other students' names
- Unregister you unless you name the exact event
- Touch exams, or overlap your schedule, unless you say so
- Go past its write budget or module allowlist

</td>
</tr>
</table>

<br>

```
 AI agent ──MCP──▶ server.py ─▶ tools.py ─▶ client.py ─▶ my.epitech.eu/api
 (you ask)            │            │             │
                      │     guardrails +         └─ silent Microsoft SSO
                      │     audit log               when tokens expire
                      └─ stdio (on demand) or HTTP
```

> [!WARNING]
> Unofficial project. It talks to the same private API as the website, with your own account. Automating registrations may be restricted by your school's rules: check them first, and use it at your own risk.

## Install

Needs Python 3.10+ and an MCP-capable agent. The installer registers itself automatically with the `claude` and `codex` CLIs when they are available; otherwise it prints what to add by hand.

```bash
git clone https://github.com/matheoStattner/epiplan.git
cd epiplan
./install.sh
epiplan login          # once: log in with Microsoft in the window that opens (MFA works)
```

Then edit `~/.config/epiplan/rules.md` with your preferences, restart your agent, and ask it to check your planning (the installed skill is called `planning`).

<details>
<summary><b>What <code>install.sh</code> does</b></summary>

<br>

| Step | Result |
|---|---|
| virtualenv | `.venv/` with `playwright` and `mcp`, plus Chromium |
| command | `~/.local/bin/epiplan` (symlink) |
| config | `~/.config/epiplan/` (private, mode 700), with `rules.md` from the template |
| skill | `skills/planning` linked into the detected clients' skill directories (`~/.claude/skills/` and/or `~/.codex/skills/`, or `$CODEX_HOME/skills/` if set) |
| MCP server | `epitech-planning` registered with the detected clients, using absolute paths so it works from any folder |

Nothing is installed system-wide. Remove the symlinks, the MCP entry and the folder to uninstall.

</details>

## Use it

Ask in plain words: *"check my planning"*, *"what can I register to this week?"*, *"book me the review"*. A typical run:

1. It reads your rules and the next 14 days.
2. You get a short report: already booked, open and matching your rules, to decide, to watch.
3. It asks which items you want, with choices for slots.
4. For each one: a **dry run** first (checks module, window, seats, slot, overlaps), then the real call, which your agent asks you to approve.
5. A recap of what was done and what was refused.

CLI for quick checks:

```bash
epiplan whoami
epiplan list --days 7 --open-only
epiplan serve --http 8765        # run the MCP server by hand
```

## Tools

<table>
<tr><th align="left">Tool</th><th align="left">What it does</th></tr>
<tr><td><code>get_profile</code></td><td>Cursus, semester, school year (no name or login: identity stays out of the agent's context)</td></tr>
<tr><td><code>list_modules</code></td><td>Modules of the year, registration state and deadline</td></tr>
<tr><td><code>list_events</code></td><td>Activities over any date range: seats, closing time, slots, module</td></tr>
<tr><td><code>my_schedule</code></td><td>Events you are registered to</td></tr>
<tr><td><code>get_event</code></td><td>One event: your state, module state, slots (anonymised: <code>taken</code> / <code>mine</code>)</td></tr>
<tr><td><code>register_event</code></td><td>Register to an event or one slot of it</td></tr>
<tr><td><code>unregister_event</code></td><td>Cancel a registration (inside the window only)</td></tr>
<tr><td><code>register_module</code></td><td>Register to a module (allowlisted unit codes only, untested live)</td></tr>
</table>

Dates are `Europe/Paris`; a bare date means that day at midnight. There is deliberately no module-unregistration tool.

## Safety

The guardrails live in `tools.py`, not in a prompt, so a confused agent can't talk its way around them.

| Guardrail | Detail |
|---|---|
| Dry run by default | Write tools do nothing unless called with `dry_run=False` |
| Kill switch | Real writes are refused unless `EPIPLAN_ENABLE_WRITES=1` |
| Write budget | `EPIPLAN_MAX_WRITES` real writes per server process (default 5) |
| Module allowlist | `register_module` only for unit codes in `EPIPLAN_MODULE_ALLOWLIST` |
| Pre-checks | module registered, window open, not full, not past, slot free, **no overlap**, exams refused |
| Audit log | every attempt, with a mandatory `reason`, in `~/.config/epiplan/audit.jsonl` |
| Read-only mode | `epiplan serve --read-only` does not register write tools at all |

Your agent's own permission prompt on each real write is the last line of defence. Don't set "always allow" on the write tools.

<details>
<summary><b>Configuration</b></summary>

<br>

| Variable | Default | Purpose |
|---|---|---|
| `EPIPLAN_ENABLE_WRITES` | unset | Must be `1` for real writes |
| `EPIPLAN_MAX_WRITES` | `5` | Real writes per process |
| `EPIPLAN_MODULE_ALLOWLIST` | empty | Unit codes `register_module` may use, comma-separated |
| `EPIPLAN_TOKENS` | `~/.config/epiplan/tokens.json` | Internal access/refresh tokens |
| `EPIPLAN_SESSION` | `~/.config/epiplan/session.json` | Saved Microsoft browser session |
| `EPIPLAN_AUDIT` | `~/.config/epiplan/audit.jsonl` | Audit log |

`tokens.json` and `session.json` are mode `0600` and as sensitive as your login: never commit or share them.

</details>

## How login works

On macOS, epiplan uses installed Google Chrome when available, so the login can
use the system's saved passkeys. Other installations use bundled Chromium.
Set `EPIPLAN_BROWSER=chromium` to choose bundled Chromium explicitly, or use
another installed Playwright browser channel through `EPIPLAN_BROWSER`.

The site signs you in with Microsoft Entra ID. Its internal refresh token only survives a few days of inactivity (measured: dead after about 67 hours), so pasting a token isn't reliable. epiplan uses your **Microsoft session** instead, which lasts much longer.

1. `epiplan login` opens a browser once. You sign in normally; the state is saved to `session.json`.
2. When the API refresh fails, `client.py` runs a headless browser with that state, clicks the Office 365 button, lets Microsoft's silent SSO do its job, captures the new tokens and saves them back, along with the renewed cookies.
3. If the Microsoft session itself has expired, you get a clear error: run `epiplan login` again.

Fallback chain: access token → `POST /auth/refresh` → silent SSO → stop with an error. No retry loops.

## Run it on a server

`session.json` is the only state to move.

```bash
# on your machine
epiplan login
scp ~/.config/epiplan/session.json server:/srv/epiplan/data/

# on the server
docker build -t epiplan .
docker run -d --name epiplan -v /srv/epiplan/data:/data -p 127.0.0.1:8765:8765 \
  -e EPIPLAN_ENABLE_WRITES=1 epiplan
```

- Keep `/data` on a **persistent, writable** volume: the session is rewritten after each login.
- Never use the same `session.json` on two machines at once (cookie and token rotation).
- The HTTP endpoint has **no authentication**: keep it on localhost or a private network, or put an authenticating proxy in front.
- Not known yet: how long a Microsoft session lives for your tenant, and whether a new IP or country triggers MFA again. If so, `refresh` fails with a clear message: log in locally and copy the file again.

## Add-ons

Optional features that live on their own branch, so `main` stays small.

| Add-on | Branch | What it does |
|---|---|---|
| Apple Calendar (macOS) | [`addon/apple-calendar`](https://github.com/daniilsys/epiplan/tree/addon/apple-calendar) | Checks your Apple Calendar for conflicts before registering, copies chosen activities (with room and reminders) into it, and adds personal events. Local EventKit only, nothing leaves your Mac. Built by [@matheoStattner](https://github.com/matheoStattner). |

To use one, install from its branch instead of `main`:

```bash
git clone -b addon/apple-calendar https://github.com/daniilsys/epiplan.git
cd epiplan && ./install.sh
epiplan calendar connect
```

Or, from an existing clone: `git fetch origin && git switch addon/apple-calendar`, then restart your agent. Setup details are in `docs/APPLE_CALENDAR.md` on that branch.

## Repository

| Path | Role |
|---|---|
| `server.py` | MCP server (stdio / HTTP), error mapping |
| `tools.py` | The tools and their guardrails |
| `client.py` | HTTP client: bearer auth, locked refresh rotation, SSO fallback |
| `epilogin.py` | Microsoft login with Playwright (`setup`, `refresh`) |
| `epiplan.py` | Small CLI (`whoami`, `list`, `register`) |
| `bin/epiplan` | The `epiplan` command |
| `skills/planning/` | Agent skill: report, questions, confirmations |
| `docs/API.md` | Reverse-engineering notes: endpoints, data model, business rules |
| `install.sh`, `Dockerfile` | Local install, server image |

## Status

- Read tools tested against the live API; the MCP server tested end to end over stdio; dry-run guardrails verified.
- Event registration and unregistration (no slots) tested by hand and reverted.
- Not tested live: module registration, slot registration against the API's own refusals (full event, overlap), deployment from another IP.

API notes and open questions are in [docs/API.md](docs/API.md). Issues and pull requests welcome.

## License

[MIT](LICENSE)
