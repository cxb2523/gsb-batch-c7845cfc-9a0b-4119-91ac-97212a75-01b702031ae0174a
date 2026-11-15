# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Example MCP Client — calls the custom server's bq_query and gcs_list tools.

Start the server first (in a separate terminal):
  python main.py

Then run this client:
  python client_example.py

For a deployed Cloud Run server, set the SERVER_URL env var:
  SERVER_URL=https://your-cloud-run-url.run.app python client_example.py
"""

import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8080/mcp")

# For Cloud Run or other authenticated endpoints, add Authorization header:
#   headers = {"Authorization": f"Bearer {gcloud_token}"}
# For local testing, no auth headers are needed.
HEADERS = {}


async def call_tool(session, tool_name, arguments):
    """Call a tool and return its text output."""
    result = await session.call_tool(tool_name, arguments=arguments)
    for block in result.content:
        if getattr(block, "text", None):
            return block.text
    return "(no text output)"


async def main():
    print(f"=== MCP Client Example ===")
    print(f"Server: {SERVER_URL}\n")

    async with streamablehttp_client(SERVER_URL, headers=HEADERS) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"[OK] Connected to: {init.serverInfo.name} v{init.serverInfo.version}")

            # List available tools
            tools_result = await session.list_tools()
            print(f"\n{len(tools_result.tools)} tools available:")
            for tool in tools_result.tools:
                desc = (tool.description or "").strip().split("\n")[0]
                print(f"  * {tool.name}: {desc[:80]}")

            # Call bq_query — runs a lightweight SQL query
            print("\n--- Calling bq_query ---")
            sql = "SELECT CURRENT_TIMESTAMP() AS now, 'hello from MCP' AS message"
            print(f"SQL: {sql}")
            bq_result = await call_tool(session, "bq_query", {"sql": sql})
            print(f"Result:\n{bq_result}")

            # Call gcs_list — lists buckets
            print("\n--- Calling gcs_list (list buckets) ---")
            gcs_result = await call_tool(session, "gcs_list", {"prefix": ""})
            print(f"Result:\n{gcs_result}")

            # Call gcs_list with a bucket prefix to list objects
            # Uncomment and set your bucket name to test object listing:
            # print("\n--- Calling gcs_list (list objects in bucket) ---")
            # gcs_result = await call_tool(
            #     session, "gcs_list", {"prefix": "your-gcs-bucket/some/path"}
            # )
            # print(f"Result:\n{gcs_result}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        print("\nIs the server running? Start it with: python main.py")
        print(f"Expected server URL: {SERVER_URL}")
