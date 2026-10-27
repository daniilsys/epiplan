#!/usr/bin/env python3
"""MCP server exposing the my.epitech.eu tools (stdio by default).

    python server.py                    # stdio, for any MCP client
    python server.py --http 8765 [--host H]   # streamable HTTP, default 127.0.0.1:8765 (put auth in front before exposing)
    python server.py --read-only        # do not register the write tools at all
"""
import argparse
import functools

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

import tools
from client import ApiError, AuthError
from apple_calendar import CalendarError


def safe(fn):
    """Return readable tool failures without violating a read tool's list schema."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except (ApiError, AuthError, CalendarError, ValueError) as e:
            raise ToolError(tools._error(e)["error"]) from e
    return wrapper


def build(read_only=False, host="127.0.0.1", port=8000):
    mcp = FastMCP("epitech-planning", host=host, port=port, instructions=(
        "Tools for the student's my.epitech.eu planning. Dates are Europe/Paris. "
        "Read both Epitech and Apple Calendar before scheduling when connected. "
        "Write tools default to dry_run=True; only pass dry_run=False after the user approves the selected activity, "
        "and always give a short `reason`. Do not display internal event or slot IDs. "
        "A successful school registration with a failed calendar copy must be retried with sync_calendar_event only."))
    for fn in tools.READ_TOOLS + ([] if read_only else tools.WRITE_TOOLS):
        mcp.tool()(safe(fn))
    return mcp


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, metavar="PORT")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--read-only", action="store_true")
    a = ap.parse_args()
    build(a.read_only, host=a.host, port=a.http or 8000).run("streamable-http" if a.http else "stdio")
