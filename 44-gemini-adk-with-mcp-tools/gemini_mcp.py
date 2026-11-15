# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Gemini + Remote MCP Tools (Automatic Function Calling).

Wires a GCP-managed remote MCP server into Gemini via the google-genai SDK.
Gemini discovers the MCP tools, decides when to call them, executes the calls
through a live ClientSession, and folds the results into its final answer.

This is the direct SDK pattern — no agent framework required.
"""

import asyncio
import os

import google.auth
from google.auth.transport.requests import Request
from google import genai
from google.genai import types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
MCP_URL = os.environ.get(
    "MCP_URL", "https://developerknowledge.googleapis.com/mcp"
)
MODEL = "gemini-2.5-flash"

# A question Gemini cannot answer reliably without grounding — forces a
# call into the Developer Knowledge MCP docs tools.
DEFAULT_PROMPT = (
    "Using official Google Cloud documentation, what is the maximum request "
    "timeout for a Cloud Run service, and how do I set it with gcloud? "
    "Cite the doc you used."
)


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


async def gemini_with_mcp_tools(prompt: str = DEFAULT_PROMPT):
    """
    Run a Gemini generate_content call with a live MCP session as a tool.

    The google-genai SDK translates MCP tool schemas into Gemini function
    declarations AND runs the full automatic function calling loop:
    model picks a tool → SDK calls it over MCP → feeds result back → repeats.

    Args:
        prompt: The user query to answer using MCP-grounded tools.

    Returns:
        dict with keys: answer (str), tools_invoked (list of str)
    """
    headers = build_auth_headers()
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")

    async with streamablehttp_client(MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"[gemini_mcp] MCP session open — {len(tools.tools)} tools exposed to Gemini:")
            for t in tools.tools:
                print(f"  * {t.name}")

            print(f"\n[gemini_mcp] Prompt: {prompt}\n")

            # THE key line: pass the live MCP ClientSession directly as a tool.
            # The SDK translates MCP tool schemas into Gemini function declarations
            # and manages the full automatic function calling loop.
            response = await client.aio.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(tools=[session]),
            )

            print("[gemini_mcp] Final answer:")
            print(response.text)

            # Surface which MCP tools the model actually invoked.
            history = getattr(response, "automatic_function_calling_history", None) or []
            invoked = [
                part.function_call.name
                for content in history
                for part in (content.parts or [])
                if getattr(part, "function_call", None)
            ]
            print("\n[gemini_mcp] MCP tools invoked by Gemini:")
            print(", ".join(invoked) if invoked else "(none — model answered directly)")

            return {"answer": response.text, "tools_invoked": invoked}


async def run_gemini_mcp():
    """Entry point for the Gemini + MCP direct integration demo."""
    print(f"=== Gemini ({MODEL}) + MCP tools from {MCP_URL} ===\n")
    try:
        return await gemini_with_mcp_tools()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        print("\nSetup checklist:")
        print("  gcloud auth application-default login")
        print(f"  gcloud services enable developerknowledge.googleapis.com aiplatform.googleapis.com --project={PROJECT_ID}")
        print(f"  gcloud beta services mcp enable developerknowledge.googleapis.com --project={PROJECT_ID}")
        print("Gotcha: 200 on initialize + 403 on tool calls = beta `services mcp enable` was missed.")
        return None
