# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
ADK Agent with Remote MCP Toolset.

Builds a Google ADK agent whose tools come from a remote GCP MCP server
(Developer Knowledge MCP) over Streamable HTTP, then runs a single query
through InMemoryRunner. The same agent works with `adk web` for an
interactive UI.
"""

import asyncio
import os

import google.auth
from google.auth.transport.requests import Request
from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.adk.tools.mcp_tool.mcp_toolset import (
    MCPToolset,
    StreamableHTTPConnectionParams,
)
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
MCP_URL = os.environ.get(
    "MCP_URL", "https://developerknowledge.googleapis.com/mcp"
)

# ADK on Vertex AI (no API key needed; uses ADC):
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")


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


def build_agent():
    """
    Build an ADK Agent with MCPToolset sourced from a remote GCP MCP server.

    MCPToolset lazily connects to the remote server and surfaces every MCP
    tool as a native ADK tool. StreamableHTTPConnectionParams matches the
    transport used by all https://<service>.googleapis.com/mcp endpoints.
    """
    docs_toolset = MCPToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=MCP_URL,
            headers=build_auth_headers(),
        )
    )

    return Agent(
        name="gcp_docs_agent",
        model="gemini-2.5-flash",
        instruction=(
            "You are a Google Cloud documentation assistant. Always use your "
            "tools to ground answers in official docs before responding. "
            "Keep answers concise and cite sources."
        ),
        tools=[docs_toolset],
    )


async def run_adk_query(query: str = "What regions support Cloud Run, and is asia-south1 one of them?"):
    """
    Run a single query through the ADK agent and print tool calls + final answer.

    Args:
        query: The user question to send to the ADK agent.

    Returns:
        str: The agent's final response text.
    """
    agent = build_agent()
    runner = InMemoryRunner(agent=agent, app_name="mcp_demo")

    session = await runner.session_service.create_session(
        app_name="mcp_demo", user_id="demo_user"
    )

    print(f"[adk_mcp_agent] User: {query}\n")
    message = types.Content(role="user", parts=[types.Part(text=query)])

    final_answer = None
    async for event in runner.run_async(
        user_id="demo_user", session_id=session.id, new_message=message
    ):
        # Surface tool activity to show the MCP round-trips.
        for part in (event.content.parts if event.content else []):
            if getattr(part, "function_call", None):
                print(f"[adk_mcp_agent] [tool call] {part.function_call.name}")
        if event.is_final_response() and event.content and event.content.parts:
            final_answer = event.content.parts[0].text
            print(f"\n[adk_mcp_agent] Agent: {final_answer}")

    return final_answer


async def run_adk_mcp_agent():
    """Entry point for the ADK + MCP agent demo."""
    print(f"=== ADK Agent + Remote MCP ({MCP_URL}) ===\n")
    try:
        return await run_adk_query()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        print("\nSetup checklist:")
        print("  pip install google-adk")
        print("  gcloud auth application-default login")
        print(f"  gcloud services enable developerknowledge.googleapis.com aiplatform.googleapis.com --project={PROJECT_ID}")
        print(f"  gcloud beta services mcp enable developerknowledge.googleapis.com --project={PROJECT_ID}")
        print("Gotcha: 200 on initialize + 403 on tool calls = beta `services mcp enable` was missed.")
        return None
