# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
MCP Client for Local FastMCP Server

Connects to http://localhost:8080/mcp over Streamable HTTP, lists available tools,
and calls bq_query + gcs_list.

Prerequisites:
  - Start the local MCP server first (in a separate terminal):
      cd mcp_server_deploy && python server.py
  - Or set MCP_URL env var to point at a Cloud Run instance
    (with ID token auth — see cloud_run_deployer.py for that variant).

Usage:
  python mcp_client.py
  MCP_URL=http://localhost:8080/mcp python mcp_client.py
"""

import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

# Local server — no Authorization header needed locally.
# For Cloud Run (--no-allow-unauthenticated), inject an ID token header instead.
LOCAL_MCP_URL = os.environ.get("MCP_URL", "http://localhost:8080/mcp")


async def list_tools(session: ClientSession) -> None:
    """List all tools exposed by the server."""
    tools_result = await session.list_tools()
    print(f"--- {len(tools_result.tools)} tool(s) available ---")
    for tool in tools_result.tools:
        desc = (tool.description or "").strip().split("\n")[0]
        print(f"  * {tool.name}: {desc}")


async def call_bq_query(session: ClientSession, sql: str) -> None:
    """Call the bq_query tool with the given SQL and print results."""
    print(f"\n--- Calling bq_query('{sql}') ---")
    result = await session.call_tool("bq_query", arguments={"sql": sql})
    for block in result.content:
        if getattr(block, "text", None):
            print(block.text)


async def call_gcs_list(session: ClientSession, prefix: str = "") -> None:
    """Call the gcs_list tool to list buckets/objects."""
    print(f"\n--- Calling gcs_list(prefix='{prefix}') ---")
    result = await session.call_tool("gcs_list", arguments={"prefix": prefix})
    for block in result.content:
        if getattr(block, "text", None):
            print(block.text)


async def run_client(mcp_url: str = LOCAL_MCP_URL) -> None:
    """Connect to the MCP server and demonstrate tool calls."""
    print(f"=== MCP client connecting to: {mcp_url} ===\n")

    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"[OK] Connected to server: {init.serverInfo.name}\n")

            await list_tools(session)
            await call_bq_query(session, "SELECT 1 AS ok")
            await call_gcs_list(session, "")


def main():
    try:
        asyncio.run(run_client())
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        print("\nIs the local MCP server running? In another terminal:")
        print("  cd mcp_server_deploy && python server.py")
        print("Then re-run:  python mcp_client.py")


if __name__ == "__main__":
    main()
