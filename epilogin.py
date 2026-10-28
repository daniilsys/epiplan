#!/usr/bin/env python3
"""Log in to my.epitech.eu through your Microsoft session, with a single portable state file.

    epilogin.py setup     # visible browser: log in by hand (MFA works), writes session.json
    epilogin.py refresh   # headless: replays the SSO from session.json, writes tokens.json

Portability: `session.json` (Microsoft cookies + MSAL localStorage) is the only state to move.
Copy it (scp, Docker secret...) to a server, point EPIPLAN_SESSION at it, and `refresh` works
without a visible browser. The file is rewritten after every successful login (renewed
cookies). Treat it like a password.

Env: EPIPLAN_SESSION (default ~/.config/epiplan/session.json),
EPIPLAN_TOKENS (default ~/.config/epiplan/tokens.json, shared with client.py).
"""
import json
import os
import sys
from pathlib import Path

SITE = "https://my.epitech.eu"
SESSION = Path(os.environ.get("EPIPLAN_SESSION", "~/.config/epiplan/session.json")).expanduser()
TOKENS = Path(os.environ.get("EPIPLAN_TOKENS", "~/.config/epiplan/tokens.json")).expanduser()
AUTH_PATHS = ("/api/auth/token", "/api/auth/refresh")


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def open_context(p, headless, state):
    # Use installed Chrome for macOS passkeys; Chromium remains a fallback.
    channel = os.environ.get("EPIPLAN_BROWSER")
    if channel is None and sys.platform == "darwin":
        if Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists():
            channel = "chrome"
    launch_options = {"headless": headless}
    if channel and channel != "chromium":
        launch_options["channel"] = channel
    browser = p.chromium.launch(**launch_options)
    kw = {}
    if state:
        data = json.loads(Path(state).read_text())
        # The SPA keeps its internal tokens in localStorage["@account"] and reuses them without
        # asking Microsoft. Drop them to force the SSO + /auth/token exchange.
        for o in data.get("origins", []):
            o["localStorage"] = [i for i in o["localStorage"] if i["name"] != "@account"]
        kw["storage_state"] = data
    return browser, browser.new_context(**kw)


def capture_tokens(page, timeout_s, interactive=False):
    """Attend que la SPA appelle /auth/token ou /auth/refresh et renvoie le couple."""
    found = {}

    def on_response(r):
        if r.request.method == "POST" and r.url.startswith(SITE) and r.url.split("?")[0].endswith(AUTH_PATHS):
            try:
                body = r.json()
            except Exception:
                return
            if isinstance(body, dict) and body.get("access_token") and body.get("refresh_token"):
                found.update(access_token=body["access_token"], refresh_token=body["refresh_token"])

    page.on("response", on_response)
    page.goto(SITE, wait_until="domcontentloaded")
    clicked = False
    for _ in range(timeout_s * 4):
        if found:
            break
        if not clicked and not interactive:
            btn = page.get_by_text("Office 365", exact=False)
            if btn.count():
                btn.first.click()
                clicked = True
        page.wait_for_timeout(250)
    return found or None


def cmd_setup():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, ctx = open_context(p, headless=False, state=None)
        page = ctx.new_page()
        print("Log in in the browser window (tick \"Stay signed in\"). Waiting for the login to finish...")
        tokens = capture_tokens(page, timeout_s=600, interactive=True)
        if not tokens:
            sys.exit("No token exchange seen. Login not completed?")
        write_private(SESSION, json.dumps(ctx.storage_state()))
        write_private(TOKENS, json.dumps(tokens))
        browser.close()
    print(f"OK. Session -> {SESSION}, tokens -> {TOKENS}")


def cmd_refresh():
    from playwright.sync_api import sync_playwright

    if not SESSION.exists():
        sys.exit(f"{SESSION} not found. Run `epiplan login` (or copy the file from another machine).")
    with sync_playwright() as p:
        browser, ctx = open_context(p, headless=True, state=SESSION)
        page = ctx.new_page()
        tokens = capture_tokens(page, timeout_s=45)
        if not tokens:
            where = page.url.split("?")[0]
            browser.close()
            sys.exit(f"Silent login failed (page: {where}). Microsoft session expired: run `epiplan login` again.")
        write_private(SESSION, json.dumps(ctx.storage_state()))
        write_private(TOKENS, json.dumps(tokens))
        browser.close()
    print("Tokens renewed through SSO.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"setup": cmd_setup, "refresh": cmd_refresh}.get(
        cmd, lambda: sys.exit(__doc__)
    )()
