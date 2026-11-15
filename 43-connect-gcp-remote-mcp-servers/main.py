# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Connect to GCP Remote MCP Servers.

Demonstrates connecting to two Google Cloud managed MCP endpoints:
  1. Developer Knowledge MCP — GCP documentation search/retrieval
  2. BigQuery Managed MCP — dataset listing and SQL execution

Both servers use the same Streamable HTTP transport and ADC authentication
pattern, making this the universal template for any googleapis.com/mcp endpoint.

Run: python main.py
"""

import asyncio
import os

from developer_knowledge_mcp import run_developer_knowledge_mcp
from bigquery_mcp import run_bigquery_mcp

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")


async def main():
    print("=" * 78)
    print("GCP Remote MCP Servers — Connection Demo")
    print(f"Project: {PROJECT_ID}")
    print("=" * 78)

    # Stage 1: Developer Knowledge MCP
    print("\n### SERVER 1: Developer Knowledge MCP ###\n")
    dk_result = await run_developer_knowledge_mcp()

    print()

    # Stage 2: BigQuery Managed MCP
    print("### SERVER 2: BigQuery Managed MCP ###\n")
    bq_result = await run_bigquery_mcp()

    print("\n" + "=" * 78)
    print("Summary")
    if dk_result:
        print(f"  Developer Knowledge tools : {dk_result.get('tools', [])}")
        print(f"  Answer preview            : {str(dk_result.get('answer', ''))[:80]}...")
    else:
        print("  Developer Knowledge MCP   : failed (check setup — see errors above)")

    if bq_result:
        print(f"  BigQuery MCP tools        : {bq_result.get('tools', [])}")
        print(f"  BQ result preview         : {str(bq_result.get('result', ''))[:80]}...")
    else:
        print("  BigQuery MCP              : failed (check setup — see errors above)")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
