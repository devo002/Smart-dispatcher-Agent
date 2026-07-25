"""Proves the MCP round-trip actually works: spins up mcp_server.py as a real
subprocess, connects a genuine MCP client to it over stdio, lists the tools it
advertises, and calls check_inventory_tool the same way Claude Desktop or any
other MCP client would.

Run with:
    python -m scripts.mcp_inventory_client_demo [PART_ID]
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.empire_dispatcher.config import REPO_ROOT


async def main(part_id: str) -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.empire_dispatcher.mcp_server"],
        cwd=str(REPO_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("Tools advertised by the MCP server:")
            for t in tools.tools:
                print(f"  - {t.name}")
            print()

            print(f"Calling check_inventory_tool(part_id={part_id!r}) over MCP...")
            result = await session.call_tool("check_inventory_tool", {"part_id": part_id})
            for block in result.content:
                if block.type == "text":
                    print(block.text)


if __name__ == "__main__":
    requested_part = sys.argv[1] if len(sys.argv) > 1 else "HUA-AC-ISO-V2"
    asyncio.run(main(requested_part))
