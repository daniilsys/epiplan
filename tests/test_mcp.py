"""Exercise real MCP framing and output schemas without reading personal calendars."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def inspect(self, read_only):
        with tempfile.TemporaryDirectory() as temp:
            args = [str(ROOT / "server.py")] + (["--read-only"] if read_only else [])
            env = {**os.environ, "EPIPLAN_CALENDAR_CONFIG_DIR": temp,
                   "EPIPLAN_CALENDAR_HELPER": str(Path(temp) / "missing-helper")}
            params = StdioServerParameters(command=sys.executable, args=args, env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.assertEqual(len(listing.tools), 9 if read_only else 18)
                    for tool in listing.tools:
                        if "dry_run" in tool.inputSchema["properties"]:
                            self.assertTrue(tool.inputSchema["properties"]["dry_run"]["default"])
                    self.assertEqual("create_external_event" in {t.name for t in listing.tools}, not read_only)
                    failure = await session.call_tool("list_apple_calendars", {})
                    self.assertTrue(failure.isError)
                    message = " ".join(c.text for c in failure.content if hasattr(c, "text"))
                    self.assertNotIn("validation error", message)
                    self.assertIn("Apple Calendar", message)

    async def test_read_only_hides_writes_and_errors_are_readable(self):
        await self.inspect(True)

    async def test_all_tools_have_valid_mcp_schemas_and_dry_run_defaults(self):
        await self.inspect(False)
