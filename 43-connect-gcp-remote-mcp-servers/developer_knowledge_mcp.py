# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Connect to Google Developer Knowledge MCP Server.

Demonstrates the baseline pattern for connecting to any GCP-managed remote MCP
server over Streamable HTTP. Lists available tools and calls the first one with
a sample Cloud Run question.

Setup (BOTH commands required):
    gcloud services enable developerknowledge.googleapis.com --project=<PROJECT>
    gcloud beta services mcp enable developerknowledge.googleapis.com --project=<PROJECT>

Gotcha: missing the second command yields 200 on initialize but 403 on tool calls.
"""

import asyncio
import os

import google.auth
from google.auth.transport.requests import Request
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
MCP_URL = os.environ.get(
    "DEVELOPER_KNOWLEDGE_MCP_URL",
    "https://developerknowledge.googleapis.com/mcp",
)


def build_auth_headers():
    """Build OAuth bearer headers from Application Default Credentials (ADC).

    Remote GCP MCP servers authenticate exactly like any other Google API:
    a standard OAuth2 access token in the Authorization header. ADC resolves
    credentials from `gcloud auth application-default login` locally, or the
    attached service account on Cloud Run / GCE.
    """
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    return {
        "Authorization": f"Bearer {credentials.token}",
        "x-goog-user-project": PROJECT_ID,
    }


async def query_developer_knowledge(question: str = "How do I deploy a container to Cloud Run?"):
    """
    Connect to Developer Knowledge MCP, list tools, and call the first tool.

    Args:
        question: Question to ask via the first available string-input tool.

    Returns:
        dict with keys: server_name, tools (list), answer (str)
    """
    headers = build_auth_headers()
    result = {"server_name": None, "tools": [], "answer": None}

    async with streamablehttp_client(MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            result["server_name"] = init.serverInfo.name
            print(f"[developer_knowledge_mcp] Connected: {init.serverInfo.name} "
                  f"v{init.serverInfo.version}")

            tools_result = await session.list_tools()
            result["tools"] = [t.name for t in tools_result.tools]
            print(f"[developer_knowledge_mcp] {len(tools_result.tools)} tools available:")
            for tool in tools_result.tools:
                desc = (tool.description or "").strip().split("\n")[0]
                print(f"  * {tool.name}: {desc[:90]}")

            if not tools_result.tools:
                print("[developer_knowledge_mcp] No tools exposed by this server.")
                return result

            # Call the first tool that accepts a string argument.
            tool = tools_result.tools[0]
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            string_params = [k for k, v in props.items() if v.get("type") == "string"]

            print(f"\n[developer_knowledge_mcp] Calling tool: {tool.name}")
            if string_params:
                args = {string_params[0]: question}
                print(f"  Question: {question}")
                call_result = await session.call_tool(tool.name, arguments=args)
                for block in call_result.content:
                    if getattr(block, "text", None):
                        answer = block.text[:1500]
                        result["answer"] = answer
                        print(f"\n[developer_knowledge_mcp] Answer:\n{answer}")
                        break
            else:
                print("  No obvious string argument. Input schema:")
                print(f"  {schema}")

    return result


async def run_developer_knowledge_mcp():
    """Entry point for Developer Knowledge MCP demo."""
    print(f"=== Developer Knowledge MCP: {MCP_URL} ===")
    print(f"Project: {PROJECT_ID}\n")

    try:
        return await query_developer_knowledge()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        print("\nSetup fix (BOTH commands required):")
        print(f"  gcloud services enable developerknowledge.googleapis.com --project={PROJECT_ID}")
        print(f"  gcloud beta services mcp enable developerknowledge.googleapis.com --project={PROJECT_ID}")
        print("Gotcha: 200 on initialize + 403 on tool calls = beta `services mcp enable` was missed.")
        return None
