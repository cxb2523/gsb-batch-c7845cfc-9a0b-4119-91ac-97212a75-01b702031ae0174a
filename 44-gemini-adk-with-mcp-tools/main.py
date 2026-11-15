# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Gemini + ADK with MCP Tools.

Demonstrates two patterns for integrating MCP tools with Gemini:

  1. Direct SDK pattern (gemini_mcp.py): pass a live MCP ClientSession directly
     to generate_content() — automatic function calling handles the loop.

  2. ADK agent pattern (adk_mcp_agent.py): wrap MCP tools in MCPToolset and
     wire to an ADK Agent — agent framework manages planning, tool selection,
     and multi-turn reasoning.

Run: python main.py
"""

import asyncio
import os

from gemini_mcp import run_gemini_mcp
from adk_mcp_agent import run_adk_mcp_agent

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
MCP_URL = os.environ.get("MCP_URL", "https://developerknowledge.googleapis.com/mcp")


async def main():
    print("=" * 78)
    print("Gemini & ADK with Remote MCP Tools")
    print(f"Project: {PROJECT_ID} | MCP: {MCP_URL}")
    print("=" * 78)

    # Pattern 1: Gemini direct SDK + MCP session as tool
    print("\n### PATTERN 1: Gemini SDK — Direct MCP Integration ###\n")
    gemini_result = await run_gemini_mcp()

    print()

    # Pattern 2: ADK Agent with MCPToolset
    print("### PATTERN 2: ADK Agent with MCPToolset ###\n")
    adk_result = await run_adk_mcp_agent()

    print("\n" + "=" * 78)
    print("Summary")
    if gemini_result:
        invoked = gemini_result.get("tools_invoked", [])
        print(f"  Gemini MCP tools invoked : {', '.join(invoked) if invoked else '(answered directly)'}")
    else:
        print("  Gemini MCP               : failed (check setup above)")

    if adk_result:
        print(f"  ADK agent answer preview : {str(adk_result)[:100]}...")
    else:
        print("  ADK agent                : failed (check setup above)")
    print("=" * 78)

    print()
    print("To run the ADK agent interactively in the dev UI:")
    print("  Set root_agent = build_agent() in an ADK agent package, then:")
    print("  adk web")


if __name__ == "__main__":
    asyncio.run(main())
