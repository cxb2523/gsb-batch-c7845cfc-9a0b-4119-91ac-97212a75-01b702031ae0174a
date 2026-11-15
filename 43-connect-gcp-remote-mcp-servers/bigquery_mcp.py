# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
BigQuery Managed Remote MCP Server Client.

Connects to BigQuery's managed MCP endpoint, lists tools, and calls a
dataset-listing or query-style tool against your project.

Setup (BOTH commands required):
    gcloud services enable bigquery.googleapis.com --project=<PROJECT>
    gcloud beta services mcp enable bigquery.googleapis.com --project=<PROJECT>

Gotcha: the endpoint returns 200 on initialize even when the MCP surface is not
enabled — only a tool call proves it. A 200 then 403 means the second command
(`beta services mcp enable`) was missed.
"""

import asyncio
import os

import google.auth
from google.auth.transport.requests import Request
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
BQ_MCP_URL = "https://bigquery.googleapis.com/mcp"


def build_auth_headers():
    """Build OAuth bearer headers from Application Default Credentials."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    return {
        "Authorization": f"Bearer {credentials.token}",
        "x-goog-user-project": PROJECT_ID,
    }


def print_403_fix():
    print("\n[FIX] Tool call rejected (403). The MCP surface for BigQuery is")
    print("not enabled on this project. Run BOTH commands:")
    print(f"  gcloud services enable bigquery.googleapis.com --project={PROJECT_ID}")
    print(f"  gcloud beta services mcp enable bigquery.googleapis.com --project={PROJECT_ID}")
    print("(200 on initialize + 403 on tool calls = `beta services mcp enable` was missed.)")


async def query_bigquery_mcp():
    """
    Connect to BigQuery Managed MCP, list tools, and call dataset-listing or query tool.

    Returns:
        dict with keys: tools (list of tool names), result (str output)
    """
    headers = build_auth_headers()
    outcome = {"tools": [], "result": None}

    async with streamablehttp_client(BQ_MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("[bigquery_mcp] Initialized (200 here does NOT prove the MCP")
            print("               surface is enabled — only a tool call proves it).")

            tools_result = await session.list_tools()
            outcome["tools"] = [t.name for t in tools_result.tools]
            print(f"\n[bigquery_mcp] {len(tools_result.tools)} tools available:")
            for tool in tools_result.tools:
                desc = (tool.description or "").strip().split("\n")[0]
                print(f"  * {tool.name}: {desc[:90]}")

            names = [t.name for t in tools_result.tools]
            target = next((n for n in names if "dataset" in n.lower()), None)
            query_tool = next(
                (n for n in names if "query" in n.lower() or "sql" in n.lower()), None
            )

            try:
                if target:
                    print(f"\n[bigquery_mcp] Calling {target} (project_id={PROJECT_ID})")
                    result = await session.call_tool(target, arguments={"project_id": PROJECT_ID})
                elif query_tool:
                    print(f"\n[bigquery_mcp] Calling {query_tool} (SELECT 1)")
                    result = await session.call_tool(
                        query_tool,
                        arguments={"project_id": PROJECT_ID, "query": "SELECT 1 AS ok"},
                    )
                else:
                    print("\n[bigquery_mcp] No dataset/query tool found; nothing to call.")
                    return outcome

                for block in result.content:
                    if getattr(block, "text", None):
                        outcome["result"] = block.text[:1500]
                        print(f"\n[bigquery_mcp] Result:\n{outcome['result']}")
                        break

                if getattr(result, "isError", False):
                    print_403_fix()

            except Exception as exc:  # noqa: BLE001
                print(f"\n[bigquery_mcp] ERROR on tool call: {exc}")
                print_403_fix()

    return outcome


async def run_bigquery_mcp():
    """Entry point for BigQuery MCP demo."""
    print(f"=== BigQuery Remote MCP Server ===")
    print(f"Endpoint: {BQ_MCP_URL}")
    print(f"Project : {PROJECT_ID}\n")

    try:
        return await query_bigquery_mcp()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        print_403_fix()
        return None
