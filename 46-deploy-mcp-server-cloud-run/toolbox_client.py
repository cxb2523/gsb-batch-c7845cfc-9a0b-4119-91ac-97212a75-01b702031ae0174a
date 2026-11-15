# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
MCP Toolbox for Databases — BigQuery tools.yaml + Python client

MCP Toolbox (github.com/googleapis/genai-toolbox) is an open-source MCP server
that turns declarative YAML into database tools — no server code to write.
It speaks MCP natively, so ADK, Gemini, or any MCP client can consume it.
toolbox-core is the direct Python SDK.

This module:
  1. Writes tools.yaml (BigQuery source + two tools)
  2. Prints how to run the Toolbox binary or container
  3. Attempts a live call via toolbox-core SDK (skips gracefully if not running)

NOTE: Toolbox binary must be running for live calls.
      Download: https://github.com/googleapis/genai-toolbox/releases

Usage:
  python toolbox_client.py
  GOOGLE_CLOUD_PROJECT=your-project-id python toolbox_client.py
"""

import asyncio
import os
from pathlib import Path

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
TOOLBOX_URL = os.environ.get("TOOLBOX_URL", "http://127.0.0.1:5000")


def build_tools_yaml(project_id: str) -> str:
    """Return the tools.yaml content for a BigQuery source."""
    return f"""\
sources:
  bq-source:
    kind: bigquery
    project: {project_id}

tools:
  execute_sql:
    kind: bigquery-execute-sql
    source: bq-source
    description: Execute an arbitrary SQL statement against BigQuery.

  search_datasets:
    kind: bigquery-list-dataset-ids
    source: bq-source
    description: List the BigQuery dataset IDs available in the project.

toolsets:
  bq-toolset:
    - execute_sql
    - search_datasets
"""


def write_tools_yaml(project_id: str, path: str = "./tools.yaml") -> Path:
    """Write the generated tools.yaml to disk and return its Path."""
    yaml_path = Path(path)
    content = build_tools_yaml(project_id)
    yaml_path.write_text(content)
    print(f"[written] {yaml_path.resolve()}\n")
    print(content)
    return yaml_path


def print_run_instructions(toolbox_url: str) -> None:
    """Print instructions for starting the Toolbox binary or container."""
    print("=== Run the Toolbox server (pick one) ===")
    print("# Binary (https://github.com/googleapis/genai-toolbox/releases):")
    print("  ./toolbox --tools-file tools.yaml")
    print("# Container:")
    print("  docker run --rm -p 5000:5000 \\")
    print("    -v $(pwd)/tools.yaml:/app/tools.yaml \\")
    print("    -v ~/.config/gcloud:/root/.config/gcloud \\")
    print("    us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox \\")
    print("    --tools-file /app/tools.yaml --address 0.0.0.0")
    print(f"\nServer listens on {toolbox_url} (MCP endpoint at /mcp).\n")


async def try_live_call(toolbox_url: str) -> None:
    """Attempt a live tool call via the toolbox-core SDK.

    Skips gracefully if toolbox-core is not installed or the server is not running.
    """
    print("=== Python client (toolbox-core SDK) ===")
    try:
        from toolbox_core import ToolboxClient  # type: ignore[import]
    except ImportError:
        print("[skip] toolbox-core not installed.  Fix: pip install toolbox-core")
        return

    try:
        async with ToolboxClient(toolbox_url) as client:
            tools = await client.load_toolset("bq-toolset")
            print(f"[OK] Loaded {len(tools)} tool(s) from {toolbox_url}:")
            for tool in tools:
                print(f"  * {tool.__name__}")

            execute_sql = await client.load_tool("execute_sql")
            result = await execute_sql(sql="SELECT 1 AS ok")
            print("\nexecute_sql('SELECT 1 AS ok') →")
            print(result)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[skip] Could not reach Toolbox at {toolbox_url} "
            f"({type(exc).__name__}: {exc})"
        )
        print("Start the server first:  ./toolbox --tools-file tools.yaml")
        print("Then re-run for the live call.")


async def run_toolbox_demo(
    project_id: str = PROJECT_ID,
    toolbox_url: str = TOOLBOX_URL,
) -> None:
    """Full Toolbox demo: write YAML, print instructions, attempt live call."""
    print(f"=== MCP Toolbox for Databases — BigQuery (project: {project_id}) ===\n")
    write_tools_yaml(project_id)
    print_run_instructions(toolbox_url)
    await try_live_call(toolbox_url)


if __name__ == "__main__":
    asyncio.run(run_toolbox_demo())
